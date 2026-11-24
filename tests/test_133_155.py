import logging
import os
import re
import shutil
import subprocess
import sys
import zlib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
NAMES = {"Chinese": "王小明", "Japanese": "山田はなこ", "Korean": "김민준", "Cyrillic": "Анна Иванова",
         "Greek": "Νίκος Παπαδόπουλος", "Hebrew": "דוד כהן", "Arabic": "محمد العلي"}
CJK = ("Chinese", "Japanese", "Korean")

STACKS = ('"Times New Roman", Times, serif', "Helvetica, Arial, sans-serif",
          '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          '"Iowan Old Style", Palatino, Georgia, serif', "sans-serif", '"Courier New", Courier, monospace')

DROID = "Droid Sans Fallback"

def has_font_for(ch, family=""):

    if not shutil.which("fc-list"):
        return False
    out = subprocess.run(["fc-list", f"{family}:charset={ord(ch):x}", "family"], capture_output=True, text=True).stdout
    return bool(out.strip())

def cjk_fonts():

    names = set()
    for k in CJK:
        out = subprocess.run(["fc-list", f":charset={ord(NAMES[k][-1]):x}", "family"],
                             capture_output=True, text=True).stdout
        names |= {fam.replace(" ", "") for line in out.splitlines() for fam in line.split(",") if fam.strip()}
    return names

need_cjk = pytest.mark.skipif(not all(has_font_for(ch, DROID) for k in CJK for ch in NAMES[k] + "ヤマダ"),
                              reason="Droid Sans Fallback does not cover Chinese, Japanese and Korean here "
                                     "(the image and CI install fonts-droid-fallback and register its Korean file)")

def missing(caplog):
    return [r.getMessage() for r in caplog.records if r.name.startswith("weasyprint") and ".notdef" in r.getMessage()]

def embedded_fonts(pdf):

    found = set(re.findall(rb"/BaseFont\s*/([A-Za-z0-9+_-]+)", pdf))
    for m in re.finditer(rb"stream\r?\n", pdf):
        try:
            found |= set(re.findall(rb"/BaseFont\s*/([A-Za-z0-9+_-]+)", zlib.decompressobj().decompress(pdf[m.end():])))
        except zlib.error:
            pass
    return {f.decode().split("+", 1)[-1] for f in found}

def render(stack, text):
    import safe_media
    import weasyprint
    html = (f"<!doctype html><meta charset='utf-8'><style>body {{ font: 12pt {stack}; }} b {{ font-weight: bold; }}"
            f"</style><p>Client: {text}</p><p><b>Client: {text}</b></p>")
    return weasyprint.HTML(string=html, url_fetcher=safe_media.pdf_fetcher()).write_pdf()

@need_cjk
@pytest.mark.parametrize("stack", STACKS)
def test_cjk_names_draw_in_every_font_stack_the_pdfs_use(stack, caplog):
    caplog.set_level(logging.WARNING, logger="weasyprint")
    pdf = render(stack, " ".join(NAMES[k] for k in CJK))
    assert not missing(caplog), missing(caplog)
    fonts = embedded_fonts(pdf)
    cjk = cjk_fonts()
    assert any(f.replace("-", "").startswith(fam) for f in fonts for fam in cjk), (fonts, cjk)

@pytest.mark.parametrize("script", [k for k in NAMES if k not in CJK])
def test_greek_cyrillic_hebrew_and_arabic_names_draw(script, caplog):
    if not has_font_for(NAMES[script][0]):
        pytest.skip(f"no font with {script} characters installed here (the image has DejaVu)")
    caplog.set_level(logging.WARNING, logger="weasyprint")
    for stack in STACKS:
        render(stack, NAMES[script])
    assert not missing(caplog), missing(caplog)

def test_the_image_installs_droid_sans_fallback_with_its_korean_file():

    korean = "<dir>/usr/share/fonts-droid-fallback/truetype</dir>"
    docker = open(os.path.join(ROOT, "Dockerfile")).read()
    assert "fonts-droid-fallback" in docker and korean in docker
    assert "noto" not in docker.lower()
    ci = open(os.path.join(ROOT, ".github", "workflows", "ci.yml")).read()
    assert re.search(r"apt-get install[^\n]*fonts-droid-fallback", ci) and korean in ci
    lic = open(os.path.join(ROOT, "THIRD_PARTY_LICENSES.md")).read()
    assert "Droid Sans Fallback" in lic and "Apache-2.0" in lic and "Noto" not in lic

@pytest.fixture(scope="module")
def studio(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("docs133")
    os.environ["CRM_DB"] = str(tmp / "crm.db")
    os.environ["CRM_STORAGE_CFG"] = str(tmp / "storage.json")
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in C.PRESETS}, environ_base=E)
    con = A.connect()
    hid = con.execute("INSERT INTO households (name) VALUES (?)", (f"{NAMES['Chinese']} {NAMES['Japanese']}",)).lastrowid
    pid = con.execute("INSERT INTO people (household_id, name, role, is_minor) VALUES (?, ?, 'child', 1)",
                      (hid, NAMES["Korean"])).lastrowid
    con.execute("INSERT INTO people (household_id, name, role) VALUES (?, ?, 'parent')", (hid, NAMES["Japanese"]))
    sid = con.execute("INSERT INTO sessions (household_id, type, shoot_date, stage) VALUES (?, 'portrait', '2027-05-03', "
                      "'booked')", (hid,)).lastrowid
    con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (?, 'Session', 300, 'custom')", (sid,))
    did = con.execute("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'release', ?, ?)",
                      (sid, pid, f"Parent: {NAMES['Japanese']}")).lastrowid
    con.commit(); con.close()
    yield A, c, hid, sid, did
    C.apply({})

@need_cjk
@pytest.mark.skipif(not shutil.which("pdftotext"), reason="needs pdftotext (poppler-utils)")
def test_the_apps_pdfs_draw_a_cjk_client_name(studio, caplog):
    A, c, hid, sid, did = studio
    caplog.set_level(logging.WARNING, logger="weasyprint")
    urls = [f"/session/{sid}/agreement.pdf", f"/doc/{did}/release.pdf", f"/session/{sid}/invoice.pdf",
            f"/session/{sid}/details.pdf", f"/client/{hid}/statement.pdf"]
    if "letters" in A.LOADED:
        urls.append(f"/letters/{sid}/print-release.pdf")
    for url in urls:
        caplog.clear()
        r = c.get(url, environ_base=E)
        assert r.status_code == 200 and r.data[:5] == b"%PDF-", (url, r.status_code)
        text = subprocess.run(["pdftotext", "-", "-"], input=r.data, capture_output=True, check=True).stdout.decode()
        assert NAMES["Chinese"] in text or NAMES["Japanese"] in text or NAMES["Korean"] in text, (url, text[:300])
        assert not missing(caplog), (url, missing(caplog))
