import email.message
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

import about

E = {"REMOTE_ADDR": "127.0.0.1"}

def meta(name, expr=None, lic=None, classifiers=()):
    m = email.message.Message()
    m["Name"] = name
    if expr is not None:
        m["License-Expression"] = expr
    if lic is not None:
        m["License"] = lic
    for c in classifiers:
        m["Classifier"] = c
    return m

def test_license_expression_comes_first():
    assert about.license_of(meta("x", "Apache-2.0 OR BSD-3-Clause", "MIT", ["License :: OSI Approved :: MIT License"])) \
        == "Apache-2.0 OR BSD-3-Clause"

def test_license_field_when_it_is_an_spdx_id():
    assert about.license_of(meta("bcrypt", lic="Apache-2.0", classifiers=["License :: OSI Approved :: Apache Software License"])) == "Apache-2.0"

def test_old_spdx_spellings_are_brought_up_to_date():
    assert about.license_of(meta("somepkg", "LGPL-2.1")) == "LGPL-2.1-only"
    assert about.spdx("(GPL-2.0+ OR MIT)") == "(GPL-2.0-or-later OR MIT)"

def test_one_license_classifier_names_it():
    assert about.license_of(meta("blinker", classifiers=["License :: OSI Approved :: MIT License"])) == "MIT"

def test_pyrage_with_no_license_metadata_is_mit():
    assert about.license_of(meta("pyrage")) == "MIT"

def test_pyphen_classifier_text_gives_way_to_its_license_file():
    m = meta("pyphen", classifiers=["License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
                                    "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)",
                                    "License :: OSI Approved :: Mozilla Public License 1.1 (MPL 1.1)"])
    assert about.license_of(m) == "GPL-2.0-or-later OR LGPL-2.1-or-later OR MPL-1.1"

def test_dateutil_dual_license():
    m = meta("python_dateutil", lic="Dual License", classifiers=["License :: OSI Approved :: BSD License",
                                                                 "License :: OSI Approved :: Apache Software License"])
    assert about.license_of(m) == "Apache-2.0 AND BSD-3-Clause"

def test_bsd_license_classifier_alone_is_not_enough():
    assert about.license_of(meta("somepkg", classifiers=["License :: OSI Approved :: BSD License"])) == ""

def test_prose_and_unknown_ids_are_not_spdx():
    for bad in ("", "Dual License", "BSD", "Apache Software", "MIT\nPermission is hereby granted", "Foo-1.0",
                "MIT OR", "AND MIT", "Copyright (c) 2020 Someone"):
        assert about.spdx(bad) == "", bad
    assert about.license_of(meta("somepkg", lic="Some long license text\nwith lines")) == ""

def test_every_locked_package_resolves():
    names = about.lock_names()
    assert "pyrage" in names and "pyphen" in names and len(names) > 20
    assert about.license_problems() == []
    for n in names:
        lic = about._license(about.md.distribution(n))
        assert lic == about.spdx(lic) and lic, (n, lic)

def test_license_problems_names_the_package(monkeypatch):
    class D:
        version = "1.0"
        metadata = meta("mystery", lic="Dual License")
    monkeypatch.setattr(about.md, "distribution", lambda n: D)
    probs = about.license_problems(["mystery"])
    assert len(probs) == 1 and probs[0].startswith("mystery 1.0: license not named cleanly") and "Dual License" in probs[0]
    monkeypatch.undo()
    assert "not installed" in about.license_problems(["no-such-package-186"])[0]

def test_about_page_shows_spdx_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    page = A.app.test_client().get("/about", environ_base=E).get_data(as_text=True)
    assert "pyrage (MIT)" in page and "()" not in page.split("Includes:")[1].split("</p>")[0]
    assert "Apache Software" not in page and "GNU General Public" not in page
    assert signed_in_client(A).get("/about", environ_base=E).status_code == 200
