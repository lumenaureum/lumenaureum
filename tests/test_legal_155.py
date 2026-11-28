import os
import re
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
SHOOT = (date.today() + timedelta(days=30)).isoformat()
BASE = ("The photographer shows no other photo from the session, and does not publish names or location with the photos"
        " the photographer shows.")
PROOFS = "Proofs, unedited files and photos I do not deliver are not included."

@pytest.fixture
def env(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={f"preset_{p}": "1" for p in ("families", "headshots", "realestate", "products")},
           environ_base=E)
    con = A.connect()
    con.execute("INSERT INTO households (id, name) VALUES (1, 'Lane')")
    con.execute("INSERT INTO households (id, name, company) VALUES (2, 'Reed', 'Northwind Goods')")
    con.execute("INSERT INTO people (id, household_id, name, role, is_minor) VALUES (1, 1, 'Dana Lane', 'parent', 0)")
    con.execute("INSERT INTO people (id, household_id, name, role, is_minor) VALUES (2, 1, 'Ava Lane', 'child', 1)")
    con.execute("INSERT INTO people (id, household_id, name, role, is_minor) VALUES (3, 2, 'Sam Reed', 'adult', 0)")
    con.commit(); con.close()
    return A, c

def session(A, hid, type_, price=300, subjects=()):
    with A.app.test_request_context():
        sid = A.create_session(hid, type_, stage="booked", shoot_date=SHOOT, subject_ids=subjects)
        if price:
            A.x("INSERT INTO lines (session_id, label, amount, kind) VALUES (?, 'Session', ?, 'custom')", (sid, price))
        A.db().commit()
    return sid

def release(A, sid, about=None, covers="", ck=""):
    with A.app.test_request_context():
        did = A.x("INSERT INTO documents (session_id, kind, about_person, signer, covers, covers_kind) VALUES (?,?,?,?,?,?)",
                  (sid, "release", about, "", covers, ck))
        A.db().commit()
    return did

def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html))).replace("&#39;", "'").replace("&amp;", "&")

def agreement(A, sid):
    with A.app.test_request_context():
        return text(A.agreement_view(sid)["custom"])

def release_text(A, did):
    with A.app.test_request_context():
        return text(A.release_view(did)["custom"])

def transfer_types(A, *keys):
    with A.app.test_request_context():
        A._save_rules(LICENSE_TYPES=list(keys))

def test_every_release_scopes_the_promise_to_the_photographer(env):
    A, c = env
    sid = session(A, 1, "family", subjects=(1,))
    r_ = release_text(A, release(A, sid, about=1))
    assert BASE in r_ and "No other photo is shown anywhere" not in r_ and "never published" not in r_
    kid = release_text(A, release(A, sid, about=2))
    assert "does not publish names, school or location with the photos the photographer shows." in kid
    con = A.connect(); con.execute("UPDATE sessions SET portfolio_no=1 WHERE id=?", (sid,)); con.commit(); con.close()
    assert ("The photographer shows no photo from the session in a portfolio, on a website, on social media, or anywhere"
            " else, and does not publish names or location with them.") in release_text(A, release(A, sid, about=1))

def test_person_on_a_business_session_agrees_to_the_clients_use(env):
    A, c = env
    sid = session(A, 2, "product")
    r_ = release_text(A, release(A, sid, covers="Jo Model", ck="guest"))
    assert ("The edited photos are delivered to Northwind Goods, who booked the session, not to me. I agree that Northwind"
            " Goods may use the photos I appear in under its license from the photographer, which covers showing and"
            " selling these products, in print and online. My name is used with the photos only if I agree in writing."
            " The photographer may show these photos in a portfolio and on a website, without my name. If I ask in"
            " writing, the photographer takes a photo I appear in down from the portfolio and website within 14 days;"
            " that does not change Northwind Goods' use.") in r_
    assert "No other photo" not in r_ and "shows no other photo" not in r_
    kid = release_text(A, release(A, sid, covers="Kit Model", ck="minor"))
    assert "not to us. We agree that Northwind Goods may use the photos Kit Model appears in" in kid
    assert "Kit Model's name and school are used with the photos only if we agree in writing." in kid

def test_real_estate_property_release_and_borrowed_location(env):
    A, c = env
    sid = session(A, 2, "realestate")
    r_ = release_text(A, release(A, sid, covers="12 Elm Street", ck="property"))
    assert "I am the owner of the property, or I may give this permission for the owner." in r_
    assert "may use the photos of the property under its license from the photographer, which covers marketing this property" in r_
    assert "without the owner's name or contact details. The property's address may be published as part of that use." in r_

def test_headshot_person_is_the_client(env):
    A, c = env
    sid = session(A, 1, "headshot", subjects=(1,))
    r_ = release_text(A, release(A, sid, about=1))
    assert "The edited photos are delivered to me, with the license set out in the session agreement." in r_ and BASE in r_

def test_transfer_release_points_to_the_agreement(env):
    A, c = env
    transfer_types(A, "product", "realestate")
    sid = session(A, 1, "family", subjects=(1,))
    r_ = release_text(A, release(A, sid, about=1))
    assert "and the copyright in them is transferred as the session agreement says." in r_
    assert "transferred to me" not in r_ and "transferred to the family" not in r_
    a_ = agreement(A, sid)
    assert ("When the balance is paid in full, I transfer to you all copyright in the final edited photos I deliver. "
            + PROOFS) in a_

def test_proofs_privacy_and_minor_wording_in_the_agreement(env, monkeypatch):
    A, c = env
    monkeypatch.setattr(A.C, "galleries_on", lambda: True)
    sid = session(A, 1, "family", subjects=(2,))
    a_ = agreement(A, sid)
    assert ("Before delivery I may show you proofs in a private gallery so you can choose favorites. Proofs are for choosing"
            " only and are not the delivered photos.") in a_
    assert "license to the final edited photos I deliver for personal use" in a_ and PROOFS in a_
    assert "I take care to keep them secure" in a_ and "I keep them secure" not in a_
    assert ("I keep business records for up to 7 years after our last session and then delete them, except what I must keep"
            " longer for taxes or legal reasons.") in a_
    assert "or delete it, except records I need to keep for taxes, legal reasons, or to show what we agreed." in a_
    assert "When a child in these photos becomes an adult, they may ask me to take down any photo of them" in a_
    assert "turns 18" not in a_
    r_ = release_text(A, release(A, sid, about=2))
    assert "I may ask for them to be deleted; the photographer keeps this release and the records the law requires." in r_

def test_no_charge_agreement_drops_the_money_sentences(env):
    A, c = env
    paid = agreement(A, session(A, 1, "family", subjects=(1,)))
    assert "A deposit, if required, holds your date" in paid and "In no case will I owe you more" in paid
    free = agreement(A, session(A, 1, "family", price=0, subjects=(1,)))
    assert "There is nothing to pay for this session: no deposit and no balance." in free
    for gone in ("A deposit, if required", "The balance is", "I refund what you have paid", "refund the part of the price",
                 "In no case will I owe you more than you have paid", "bill half"):
        assert gone not in free, gone
    assert "If photos are lost, damaged or not taken, I will reshoot at no charge if that is possible. This is your only remedy against me." in free
    assert "we move it to a new date within 12 months at no charge." in free
    assert "I am not responsible for indirect or consequential losses" in free
    transfer_types(A, "product", "realestate")
    free_t = agreement(A, session(A, 1, "family", price=0, subjects=(1,)))
    assert "When I deliver the final edited photos, I transfer to you all copyright in them. " + PROOFS in free_t
    assert "When the balance is paid in full" not in free_t

def test_no_charge_missed_session_bills_nothing(env):
    A, c = env
    import config as C
    for t in C.SESSION_TYPES:
        assert "bill" not in C.late_text(t, free=True) and "deposit" not in C.late_text(t, free=True)

def test_own_wording_that_was_rewritten_stays_as_written(env):
    A, c = env
    import wording
    own = wording.STANDARD["agreement"].replace("A deposit, if required, holds your date", "My retainer holds your date")
    with A.app.test_request_context():
        A.x("INSERT INTO settings (key, value) VALUES ('agreement_text', ?)", (own,))
        A.db().commit()
    free = agreement(A, session(A, 1, "family", price=0, subjects=(1,)))
    assert "My retainer holds your date" in free and "There is nothing to pay" not in free
    assert wording.AGREEMENT_1_3 != wording.STANDARD["agreement"] and "I keep them secure" in wording.AGREEMENT_1_3

def test_letters_name_the_final_edited_photos():
    import wording
    assert "all copyright in the final edited photos delivered from this session" in wording.STANDARD["transfer"]
    assert "Your license to the final edited photos delivered from this session" in wording.STANDARD["license"]
    for doc in ("transfer", "license"):
        assert "Proofs, unedited files and photos that are not delivered are not included." in wording.STANDARD[doc]

def test_exclusive_license_help_line(env):
    A, c = env
    sid = session(A, 2, "product")
    with A.app.test_request_context():
        url = A.stage_url(sid)
    page = c.get(url, environ_base=E).get_data(as_text=True)
    assert "Exclusive license" in page
    assert "On an exclusive license, sign your own line on the agreement too, not only the client." in page

def test_inquiry_form_privacy_line():
    from plugins import studio_page as S
    line = "What you send is used only to answer your inquiry. It is not sold or added to a mailing list."
    assert line in S.INQ_FORM and S.INQ_FORM.index(line) < S.INQ_FORM.index("</form>")

def test_people_event_under_i_own_gives_the_organizer_a_license(env):
    A, c = env
    con = A.connect()
    con.execute("INSERT INTO events (id, name, date, rights, attendees) VALUES (1, 'Staff day', ?, 'families', 'people')", (SHOOT,))
    con.commit(); con.close()
    org = ("The organizer receives a license to use each person's photos, with their name and role, in its directory, on"
           " its website, and in its press and marketing. It does not cover selling the photos or passing them to others.")
    with A.app.test_request_context():
        assert org in A.event_rights_text(1, voice="organizer") and org in A.event_rights_text(1)
        mine = A.event_rights_text(1, voice="release", person="Sam Reed", photographer="Pat Lens")
        kid = A.event_rights_text(1, voice="release", person="Kit Reed", photographer="Pat Lens", minor=True)
    assert ("I agree that the organizer may use my photos, with my name and role, in its directory, on its website, and in"
            " its press and marketing.") in mine
    assert "may use Kit Reed's photos, with Kit Reed's name and role" in kid

def test_lent_location_on_a_business_session(env):
    A, c = env
    r_ = release_text(A, release(A, session(A, 2, "product"), covers="A lent kitchen", ck="property"))
    assert "I am the owner of the property, or I may give this permission for the owner." in r_
    assert "may use the photos of the property under its license from the photographer, which covers showing and selling" in r_
    assert "The photographer does not publish the property's address with the photos." in r_
    assert "address may be published" not in r_

def test_company_booked_headshots_release(env):
    A, c = env
    sid = session(A, 2, "headshot", subjects=(3,))
    r_ = release_text(A, release(A, sid, about=3))
    assert ("The edited photos are delivered to Northwind Goods, who booked the session. Northwind Goods and I may each use"
            " my photos under the license in the session agreement, which covers professional profiles, websites, press and"
            " marketing for the people photographed and their employer. Northwind Goods may use my name and job title with"
            " them.") in r_
    assert BASE in r_ and "The photographer may show up to 2 photos" in r_

def test_personal_project_releases_and_the_trade_tick(env):
    A, c = env
    c.post("/setup/shoot", data={f"preset_{p}": "1" for p in ("families", "headshots", "realestate", "products", "personal")},
           environ_base=E)
    import config as C
    sid = session(A, 1, "portfolio", price=0, subjects=(1,))
    with A.app.test_request_context():
        A.x("INSERT INTO settings (key, value) VALUES ('photographer_name', 'Pat Lens')")
        A.db().commit()
    r_ = release_text(A, release(A, sid, about=1))
    assert ("I agree that Pat Lens may use the photos I appear in: to show them in a portfolio, on a website and on social"
            " media, to exhibit and publish them, to enter them in contests, and to sell prints of them. Pat Lens keeps the"
            " copyright. The photos are not used to advertise another business's products or services unless I agree in"
            " writing. My name is published with the photos only if I agree in writing. If I ask in writing, Pat Lens takes"
            " a photo I appear in down from the portfolio, website and social media within 14 days and does not post it"
            " again; prints already sold and copies already published or shared by others cannot be recalled.") in r_
    assert "In return" not in r_ and C.TYPE_KIND["portfolio"] == "personal"
    with A.app.test_request_context():
        url = A.stage_url(sid)
    assert "Traded for photos" in c.get(url, environ_base=E).get_data(as_text=True)
    c.post(f"/session/{sid}/trade-copies", data={"trade_copies": "1"}, environ_base=E)
    traded = release_text(A, release(A, sid, about=1))
    assert ("In return I receive copies of the edited photos I appear in, for my own personal use: to print, share and post"
            " them, not to sell them.") in traded
    prop = release_text(A, release(A, sid, covers="An old mill", ck="property"))
    assert "I agree that Pat Lens may use the photos of the property: to show them in a portfolio" in prop
    assert "The owner's name and the property's address are published only if I agree in writing." in prop

def test_license_drops_cannot_be_taken_back_and_real_estate_names_the_mls(env):
    A, c = env
    assert "cannot be taken back" not in agreement(A, session(A, 1, "family", subjects=(1,)))
    re_ = agreement(A, session(A, 2, "realestate"))
    assert ("This includes giving the listing service (MLS) and the sites it feeds the rights their rules require for the"
            " listing.") in re_
    assert "listing service" not in agreement(A, session(A, 2, "product"))

def test_deposit_says_why_it_is_kept(env):
    A, c = env
    import config as C
    paid = agreement(A, session(A, 1, "family", subjects=(1,)))
    assert "The deposit reserves your date: I turn away other work to hold it. If you cancel, I keep the deposit for that reason." in paid
    assert "the deposit is not refunded" not in paid
    no_dep = [t for t in C.SESSION_TYPES if C.has_no_deposit(t) and C.POLICY[t]["late_minutes"]]
    for t in no_dep:
        assert "I may bill for the time I held and any costs I paid, up to half of the price" in C.late_text(t)

def test_the_deposit_can_be_called_something_else(env):
    A, c = env
    import config as C
    with A.app.test_request_context():
        A.x("INSERT INTO settings (key, value) VALUES ('deposit_word', 'retainer')")
        A.db().commit()
    sid = session(A, 1, "family", subjects=(1,))
    a_ = agreement(A, sid)
    assert "A retainer, if required, holds your date" in a_ and "The retainer reserves your date" in a_
    assert not re.search(r"\bdeposit\b", a_, re.I)
    with A.app.test_request_context():
        terms = " ".join(t_ for _h, t_ in C.build_terms("family"))
        assert "retainer" in terms and not re.search(r"\bdeposit\b", terms, re.I)
    assert C.clean_deposit_word("<b>x</b>") == "" and C.clean_deposit_word("  Booking   Fee ") == "booking fee"
    with A.app.test_request_context():
        A.x("UPDATE settings SET value='advance' WHERE key='deposit_word'")
        A.db().commit()
        assert C.dw("A deposit holds your date. Deposits.") == "An advance holds your date. Advances."
    import settings_io
    assert "deposit_word" in settings_io.SETTING_KEYS
