"""The agreement and the release, end to end, for every kind of session a preset brings: the standard wording and the
owner's own wording (every [FIELD] used), the fill-ins, on the page and in the PDF. Nothing is left unfilled, the
fill-ins reach the client, the owner's text is escaped, and the credit line never appears on a signed document."""
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client  # noqa: E402

E = {"REMOTE_ADDR": "127.0.0.1"}
pytestmark = pytest.mark.skipif(not shutil.which("pdftotext"), reason="needs pdftotext (poppler-utils)")


def pdf_text(data):
    return subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, check=True).stdout.decode()


def flat(s):
    return " ".join(s.split())


@pytest.fixture(scope="module")
def studio(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("docs")
    os.environ["CRM_DB"] = str(tmp / "crm.db")
    os.environ["CRM_STORAGE_CFG"] = str(tmp / "storage.json")
    fresh_modules()
    import app as A
    import config as C
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{k}": "1" for k in C.PRESETS}, environ_base=E)
    # these tests check both kinds of document: people sessions transfer the copyright, business ones license it
    # (a fresh install now presets every type to license)
    c.post("/setup/documents", data={**{f"rights_{t['key']}": ("license" if t["kind"] == "property" or t.get("license") else "transfer")
                                        for t in C.TYPES}, "governing_law": ""}, environ_base=E)
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('photographer_name', 'Avery Lane') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    sids = {}
    for i, key in enumerate(t["key"] for t in C.TYPES if t["kind"] != "event"):
        hid = con.execute("INSERT INTO households (name) VALUES (?)", (f"Client {key}",)).lastrowid
        pid = con.execute("INSERT INTO people (household_id, name, role, is_minor) VALUES (?, ?, 'child', 1)",
                          (hid, f"Kid {key}")).lastrowid
        sid = con.execute("INSERT INTO sessions (household_id, type, shoot_date, stage) VALUES (?, ?, '2027-05-0' || ?, 'booked')",
                          (hid, key, str(i % 9 + 1))).lastrowid
        con.execute("INSERT INTO lines (session_id, label, amount, kind) VALUES (?, 'Session', 300, 'custom')", (sid,))
        did = con.execute("INSERT INTO documents (session_id, kind, about_person, signer) VALUES (?, 'release', ?, 'Parent: Pat')",
                          (sid, pid)).lastrowid
        sids[key] = (sid, did)
    con.commit(); con.close()
    yield A, C, c, sids
    C.apply({})


def docs(c, sid, did):
    out = {}
    for name, url in (("agreement", f"/session/{sid}/agreement"), ("release", f"/doc/{did}/release")):
        r = c.get(url, environ_base=E)
        assert r.status_code == 200, (url, r.status_code)
        out[name] = r.get_data(as_text=True)
        r = c.get(url + ".pdf", environ_base=E)
        assert r.status_code == 200 and r.data[:5] == b"%PDF-", url
        out[name + ".pdf"] = flat(pdf_text(r.data))
    return out


def test_standard_wording_fills_every_field_for_every_type(studio):
    A, C, c, sids = studio
    import wording
    known = {f for d in wording.FIELDS.values() for f, _ in d}
    for key, (sid, did) in sids.items():
        d = docs(c, sid, did)
        for name in ("agreement.pdf", "release.pdf"):
            left = set(re.findall(r"\[([A-Z][A-Z ]+[A-Z])\]", d[name])) & (known | {b for _, _, b in C.FILL_INS})
            assert not left, (key, name, left)
            assert "None" not in d[name].split() and "{{" not in d[name] and C.CREDIT not in d[name], (key, name)
        assert "Avery Lane" in d["agreement.pdf"] and f"Kid {key}" in d["release.pdf"]
        assert flat(C.GALLERY_PHRASE.sub("", C.fill(key, "delivery")).strip()) in d["agreement.pdf"], key   # no gallery set up: none promised


def test_own_wording_with_every_field_and_the_fill_ins(studio):
    A, C, c, sids = studio
    import wording
    for doc in ("agreement", "release"):
        own = "# Mine\n" + "\n\n".join(f"{f}=[{f}]" for f, _ in wording.FIELDS[doc]) + "\n\n<b>bold?</b> {{ 6*7 }} [TYPO FIELD]"
        assert c.post(f"/settings/wording/{doc}", data={"text": own}, environ_base=E).status_code == 302
    key = "newborn"
    sid, did = sids[key]
    form = {"questions": "\n".join(q for _, q in C.INTAKE[key]), "free_reschedule": "1", "notice_hours": "24",
            "late_minutes": "0", "delivery": "on a keepsake USB & in a gallery", "weather": "", "keep_raw_days": "182",
            "license_use": "x"}
    c.post(f"/settings/type/{key}", data=form, environ_base=E)
    try:
        d = docs(c, sid, did)
        for name in ("agreement", "release"):
            for f, _ in wording.FIELDS[name]:
                assert f"{f}=[{f}]" not in d[name + ".pdf"], (name, f)    # every known field replaced
            assert "[TYPO FIELD]" in d[name + ".pdf"] and "{{ 6*7 }}" in d[name + ".pdf"] and "42" not in d[name + ".pdf"]
            assert "&lt;b&gt;bold?&lt;/b&gt;" in d[name] and "<b>bold?" not in d[name]
            assert C.CREDIT not in d[name] and C.CREDIT not in d[name + ".pdf"]
        a = d["agreement.pdf"]
        assert "HOW YOU DELIVER=on a keepsake USB & in a gallery" in a and "HOW LONG=182 days" in a
        assert "[WEATHER PLAN]" in a                                    # an emptied fill-in shows, so it gets noticed
        assert "PERSON=Kid newborn" in d["release.pdf"] and "GUARDIAN=I am the parent" in d["release.pdf"]
        sid2, did2 = sids["food"]                                        # a licensed type: license wording, no transfer
        d2 = docs(c, sid2, did2)
        assert "promoting this business" in d2["agreement.pdf"] and "license" in d2["release.pdf"]
    finally:
        for doc in ("agreement", "release"):
            c.post(f"/settings/wording/{doc}", data={"reset": "1"}, environ_base=E)


def test_credit_line_on_sales_documents_only(studio):
    A, C, c, sids = studio
    sid, _ = sids["portrait"]
    r = c.get(f"/session/{sid}/invoice.pdf", environ_base=E)
    assert r.status_code == 200 and "lumenaureum.org" in pdf_text(r.data)


def test_details_and_licenses_follow_the_type(studio):
    """The details sheet follows the type's settings; pets get animal wording and no
    release step; headshots are licensed by default; a partner with no phone never becomes the contact; numbered
    headings in your own wording count on when a section drops out."""
    A, C, c, sids = studio
    sid, _ = sids["newborn"]
    page = c.get(f"/session/{sid}/details", environ_base=E).get_data(as_text=True)
    assert "chessboard" not in page and f"If the weather turns, {C.fill('newborn', 'weather')}." in page
    sid, _ = sids["pet"]
    page = c.get(f"/session/{sid}/details", environ_base=E).get_data(as_text=True)
    assert "Your animals" in page and "hair and makeup" not in page
    doc = c.get(f"/session/{sid}/agreement", environ_base=E).get_data(as_text=True)
    assert "I photograph your animals" in doc and "hair and makeup" not in doc
    assert "pet" not in C.NEEDS_RELEASE and "Stray fur" in str(C.build_editing("pet"))
    assert "headshot" in C.LICENSE_TYPES and "food" in C.LICENSE_TYPES and "portrait" not in C.LICENSE_TYPES
    assert "licensing them to others" in c.get(f"/session/{sids['headshot'][0]}/agreement", environ_base=E).get_data(as_text=True)
    con = A.connect()
    hid = con.execute("SELECT household_id FROM sessions WHERE id=?", (sids["engagement"][0],)).fetchone()[0]
    con.execute("UPDATE people SET role='adult', is_minor=0, phone='555-0202' WHERE household_id=?", (hid,))
    con.execute("INSERT INTO people (household_id, name, role) VALUES (?, 'Partner Without Phone', 'parent')", (hid,))
    con.commit(); con.close()
    page = c.get(f"/client/{hid}", environ_base=E).get_data(as_text=True)
    assert "555-0202" in page
    import wording
    out = str(wording.render("# 1. A\nx\n\n# 2. Minors\n[MINORS]\n\n# 3. C\ny", {"MINORS": ""}))
    assert "<h2>2. C</h2>" in out and "3." not in out


def test_new_install_shoot_step_starts_unticked(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    c = signed_in_client(A, setup_done=False)
    page = c.get("/setup/shoot", environ_base=E).get_data(as_text=True)
    assert 'name="preset_portraits" value="1">' in page and "checked" not in page.split('class="checks"')[1].split("</div>")[0]


def test_letters_per_type(studio):
    """The printed letters: a licensed type's letter quotes that type's own license (no real-estate wording on a
    headshot), a transfer type's letter transfers; the QR letter is a draft until there is a gallery link and an
    address, and only a real one is recorded as printed; SD labels render."""
    A, C, c, sids = studio
    assert "letters" in A.LOADED
    for key, want, never in (("headshot", "professional profiles", "MLS"), ("realestate", "marketing this property", "transfers to you"),
                             ("food", "promoting this business", "MLS"), ("portrait", "transfers to you all copyright", "keeps the copyright")):
        r = c.get(f"/letters/{sids[key][0]}/print-release.pdf", environ_base=E)
        assert r.status_code == 200, key
        text = flat(pdf_text(r.data))
        assert want in text and never not in text, (key, text[:400])
    assert "you are welcome to suggest favorites" in flat(pdf_text(c.get(f"/letters/{sids['headshot'][0]}/print-release.pdf", environ_base=E).data))
    sid = sids["family"][0]
    r = c.get(f"/letters/{sid}/qr-letter.pdf", environ_base=E)
    assert r.status_code == 200 and r.data[:5] == b"%PDF-"
    con = A.connect()
    assert not con.execute("SELECT 1 FROM letters_printed WHERE session_id=?", (sid,)).fetchone()      # draft: not recorded
    con.execute("UPDATE households SET address='12 Elm St\nSpringfield' WHERE id=(SELECT household_id FROM sessions WHERE id=?)", (sid,))
    con.commit(); con.close()
    before = A.app.config.get("gallery_link")
    A.app.config["gallery_link"] = lambda s_: "https://gallery.example/g/abc#k=1" if s_ == sid else None
    try:
        text = flat(pdf_text(c.get(f"/letters/{sid}/qr-letter.pdf", environ_base=E).data))
        assert "12 Elm St" in text
        con = A.connect()
        assert con.execute("SELECT 1 FROM letters_printed WHERE session_id=?", (sid,)).fetchone()
        con.close()
    finally:
        A.app.config["gallery_link"] = before
    r = c.get(f"/letters/{sid}/sd-labels.pdf", environ_base=E)
    assert r.status_code == 200 and r.data[:5] == b"%PDF-"
    assert c.get("/letters/99999/sd-labels.pdf", environ_base=E).status_code == 404


def test_imported_wording_waits_for_review_and_is_announced(studio):
    """Security pass 2026-11-11: a shared settings file may carry its own legal wording; importing it clears the
    reviewed tick and says so; a type that skips releases is named; a question key may not shadow a form field."""
    import io
    import json as _json
    A, C, c, sids = studio
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('agreement_reviewed', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    con.commit(); con.close()
    data = c.get("/settings/export.json", environ_base=E).get_json()
    data["settings"]["agreement_text"] = "# Terms\nNo refunds, ever."
    data["settings"]["agreement_reviewed"] = "1"
    data["rules"]["TYPES"].append(dict(key="beasts", label="Beasts & co", kind="portrait", minutes=60, animals=True))
    body = _json.dumps(data).encode()
    try:
        r = c.post("/settings/import", data={"back": "/settings", "file": (io.BytesIO(body), "s.json")},
                   content_type="multipart/form-data", environ_base=E, follow_redirects=True)
        page = r.get_data(as_text=True)
        assert "brings its own agreement wording" in page and "Beasts &amp; co: no photo release" in page
        con = A.connect()
        r_ = con.execute("SELECT value FROM settings WHERE key='doc_reviews'").fetchone()
        assert not r_ or not [k for k in __import__("json").loads(r_[0]) if k.startswith("agreement:")]   # its wording needs a review
        con.close()
        assert "beasts" not in C.NEEDS_RELEASE
        import settings_io
        with pytest.raises(settings_io.ImportProblem):
            settings_io._questions([["back", "Where to?"]], "q")
        import wording
        assert "<h2>A &amp; B</h2>" in str(wording.render("# A & B\nx", {}))
    finally:
        c.post("/settings/wording/agreement", data={"reset": "1"}, environ_base=E)


def _people_transfer(c, C):
    """People sessions transfer the copyright; business ones license it (an earlier test may have changed the rules)."""
    c.post("/setup/documents", data={**{f"rights_{t['key']}": ("license" if t["kind"] == "property" or t.get("license") else "transfer")
                                        for t in C.TYPES}, "governing_law": ""}, environ_base=E)


def test_document_text_is_whole(studio):
    """Every sentence of the printed legal letters and the standard wording comes through whole (a publish step that
    dropped part of a string once shipped 'effective like, with no permission needed.')."""
    A, C, c, sids = studio
    _people_transfer(c, C)
    text = flat(pdf_text(c.get(f"/letters/{sids['portrait'][0]}/print-release.pdf", environ_base=E).data))
    for want in ("transfers to you all copyright in the photos delivered from this session, effective when the session is paid in full.",
                 "You may print, share, publish, sell or change them however you like, with no permission needed.",
                 "This permission continues after this transfer."):
        assert want in text, want
    import wording
    page = c.get(f"/session/{sids['family'][0]}/agreement", environ_base=E).get_data(as_text=True)
    assert "If no new date works for you, I refund everything you have paid." in page and "This is your only remedy against me." in page
    assert wording.DISCLAIMER.startswith("The agreement, release and other documents that come with Lumen Aureum are examples only.")


def test_delivery_letters_are_editable_and_drafts_until_reviewed(studio):
    """The copyright transfer and photo license use the wording system: the owner's own wording prints, and until the
    documents are reviewed the letter says it is a draft."""
    A, C, c, sids = studio
    _people_transfer(c, C)
    sid = sids["portrait"][0]
    con = A.connect(); con.execute("DELETE FROM settings WHERE key='agreement_reviewed'"); con.commit(); con.close()
    assert "SAMPLE: example wording" in flat(pdf_text(c.get(f"/letters/{sid}/print-release.pdf", environ_base=E).data))
    c.post("/settings/wording/transfer", data={"text": "# Yours\n[PHOTOGRAPHER] hands over the photos. [FAVORITES] stay in my portfolio."}, environ_base=E)
    text = flat(pdf_text(c.get(f"/letters/{sid}/print-release.pdf", environ_base=E).data))
    assert "hands over the photos." in text and "favorite" in text and "transfers to you all copyright" not in text
    c.post("/settings/wording/transfer", data={"reset": "1"}, environ_base=E)
    assert "Copyright transfer wording" in c.get("/settings/wording/transfer", environ_base=E).get_data(as_text=True)
