"""About: what this is, who makes it, its license and where its source is (the AGPL asks that everyone using it over
a network can get the source), and what it includes: the software bill of materials, read from the packages actually
installed, so versions never drift from what runs."""
import importlib.metadata as md
import os
import re
from pathlib import Path

from flask import render_template

ROOT = Path(__file__).resolve().parent
SOURCE = "https://github.com/lumenaureum/lumenaureum"
REQS = ("requirements.txt", "requirements-sftp.txt", "requirements-s3.txt")
# Not Python packages: shipped with the app or in the container.
OTHER = [("SQLCipher", "BSD-3-Clause", "inside sqlcipher3-binary"), ("OpenSSL libcrypto", "Apache-2.0", "inside sqlcipher3-binary"),
         ("Fraunces font", "OFL-1.1", "Undercase Type"), ("Material Symbols font", "Apache-2.0", "Google"),
         ("US Census ZIP code tables", "Public domain", "US Census Bureau")]


def version():
    try:
        return (ROOT / "VERSION").read_text().strip()
    except OSError:
        return "dev"


def _name(req):
    return re.split(r"[<>=!~;\[ ]", req.strip(), maxsplit=1)[0]


def _license(dist):
    m = dist.metadata
    lic = m.get("License-Expression") or ""
    if not lic:
        cls = [c.split(" :: ")[-1] for c in (m.get_all("Classifier") or []) if c.startswith("License ::")]
        lic = " / ".join(c.replace(" License", "") for c in cls) or (m.get("License") or "")
    lic = lic.replace("GNU Lesser General Public License v2 or later (LGPLv2+)", "LGPL-2.0+")
    lic = {"Apache Software": "Apache-2.0", "MIT License": "MIT", "BSD License": "BSD", "BSD": "BSD"}.get(lic, lic)
    return lic if len(lic) <= 40 else lic.split("\n")[0][:40]


def components():
    """Every Python package the app needs (requirements + what they pull in), as installed: name, version, license."""
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
        """Open to anyone (the license and source link must be). Exact versions only for the signed-in owner: a list of
        installed versions tells a stranger which known flaws to try."""
        from flask import session
        import auth
        signed_in = bool(session.get("uid")) and auth.SESSIONS.get(session.get("sid")) == session.get("uid")
        return render_template("about.html", version=version() if signed_in else "", build=os.environ.get("LUMEN_BUILD", "") if signed_in else "",
                               source=SOURCE, parts=components(), other=OTHER, signed_in=signed_in,
                               plugins=[p_ for p_ in __import__("app").PLUGINS_LOADED if not p_["built_in"]] if signed_in else [])
