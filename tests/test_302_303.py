import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_download_gallery import E, build, env, join, row, session

def page(c, sid):
    return c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)

def closed_box(p):
    return p.split('id="closed-now"', 1)[1].split("</section>", 1)[0]

def audit(A, sid):
    con = A.connect()
    try:
        return [r[0] for r in con.execute("SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]
    finally:
        con.close()

def sql(A, q, args=()):
    con = A.connect()
    try:
        con.execute(q, args)
        con.commit()
    finally:
        con.close()

def in_request(A, fn):
    with A.app.test_request_context():
        A.db()
        out = fn()
        A.g.db.commit()
        return out

def jpeg(color="white"):
    import io
    from PIL import Image
    b_ = io.BytesIO()
    Image.new("RGB", (200, 150), color).save(b_, "JPEG")
    return b_.getvalue()

def test_a_closed_usb_session_with_no_photos_shares_a_gallery_in_three_taps(env):

    A, c, G, tmp, src, host = env
    from test_download_gallery import upload
    retouch(c, False)
    sid = session(A, "family", delivery_method="usb", stage="closed")
    p = page(c, sid)
    box = closed_box(p)
    assert ">Share a gallery</button>" in box and "data-confirm" not in box.split("Share a gallery")[0].rsplit("<form", 1)[1]

    assert ">Reopen at Delivered</span></button>" in p.split('id="more-session"', 1)[1].split("</details>", 1)[0]
    assert p.count(">Share a gallery</button>") == 1
    c.post(f"/session/{sid}/gallery/no-picks", data={"go": "1"}, environ_base=E)
    assert tuple(row(A, "SELECT stage, delivery_method FROM sessions WHERE id=?", (sid,))) == ("closed", "usb")
    p = page(c, sid)
    assert "Add the edited photos to share" in p and f'id="gal-drop-{sid}"' in p and 'data-kind="export"' in p
    for i_ in range(3):
        upload(c, sid, f"family-{i_}.jpg", jpeg(), "export")
    p = page(c, sid)
    assert ">Build and put online</button>" in p and ">Build gallery</button>" not in p
    c.post(f"/session/{sid}/gallery/build", data={"from": "uploads", "then_upload": "1"}, environ_base=E)
    join("gallery", sid)
    join("gal-up", sid)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    assert G.is_download(gid) and G.online_download(gid)
    assert tuple(row(A, "SELECT stage, delivery_method FROM sessions WHERE id=?", (sid,))) == ("closed", "usb")
    assert "Copy link" in page(c, sid)

def test_share_a_gallery_with_the_photos_in_is_one_tap(env):
    A, c, G, tmp, src, host = env
    from test_download_gallery import upload
    retouch(c, False)
    sid = session(A, "family", delivery_method="usb", stage="delivered")
    upload(c, sid, "a.jpg", jpeg(), "export")
    p = page(c, sid)
    gal_ = p.split('id="gallery"', 1)[1]

    assert ">Share a gallery</button>" in gal_ and ">Build gallery</button>" not in gal_ and "download gallery too" not in p
    c.post(f"/session/{sid}/gallery/no-picks", data={"go": "1"}, environ_base=E)
    join("gallery", sid)
    join("gal-up", sid)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    assert G.is_download(gid) and G.online_download(gid)
    assert G.SHARE_ACTION in audit(A, sid)

def test_a_closed_session_keeps_its_picks_out_of_the_way(env):

    A, c, G, tmp, src, host = env
    sid = session(A, "family", delivery_method="usb", stage="closed")
    assert ">Share a gallery</button>" in closed_box(page(c, sid))
    c.post(f"/session/{sid}/gallery/no-picks", environ_base=E)
    assert G.NO_PICKS_ACTION in audit(A, sid) and row(A, "SELECT stage FROM sessions WHERE id=?", (sid,))[0] == "closed"

def test_deliver_without_picks_is_offered_for_every_delivery_method(env):
    A, c, G, tmp, src, host = env
    for method in ("usb", "sd", "disc", "card", "link"):
        sid = session(A, "family", delivery_method=method, stage="delivered")
        assert ">Deliver without picks</button>" in page(c, sid), method
        c.post(f"/session/{sid}/gallery/no-picks", environ_base=E)
        assert tuple(row(A, "SELECT stage, delivery_method FROM sessions WHERE id=?", (sid,))) == ("delivered", method)
        assert "Build download gallery" in page(c, sid), method

def test_with_picks_in_a_usb_session_can_share_a_download_gallery_too(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "family", delivery_method="usb", stage="delivered", retouch_picks="Kitchen.jpg: brighten")
    p = page(c, sid)
    assert ">Share a gallery</button>" in p and ">Deliver without picks</button>" not in p
    assert "Retouched finals" not in p
    c.post(f"/session/{sid}/gallery/no-picks", environ_base=E)
    assert G.SHARE_ACTION in audit(A, sid) and G.NO_PICKS_ACTION not in audit(A, sid)
    assert row(A, "SELECT delivery_method FROM sessions WHERE id=?", (sid,))[0] == "usb"
    p = page(c, sid)

    assert ">Share a gallery</button>" not in p and "Retouched finals" in p and ">Use the proofs</button>" not in p
    assert "Upload the retouched finals on the Retouch row" in p
    assert "USB drive, and a download gallery" in p

def test_a_type_with_no_gallery_step_shares_one_from_a_drive_delivery(env):

    A, c, G, tmp, src, host = env
    sid = session(A, "realestate", delivery_method="sd", stage="delivered")
    p = page(c, sid)
    assert "No gallery: the files go on an SD card." in p and ">Share a gallery</button>" in p
    c.post(f"/session/{sid}/gallery/no-picks", environ_base=E)
    assert "Build download gallery from folder" in page(c, sid)
    build(c, sid, src)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    assert G.is_download(gid)

def retouch(c, on):
    return c.post("/settings/retouch", data={"offer_retouch": "1"} if on else {}, environ_base=E)

def test_offer_retouching_is_on_by_default_and_a_switch_on_prices(env):
    A, c, G, tmp, src, host = env
    assert A.C.OFFER_RETOUCH is True
    p = c.get("/settings?tab=prices", environ_base=E).get_data(as_text=True)
    assert '<input type="checkbox" id="f-offer_retouch" name="offer_retouch" value="1" checked>Offer retouching' in p

    form_ = p.split('id="retouch"', 1)[0].rsplit("<form", 1)[1]
    assert "data-ui-autosave data-ui-autosave-reload" in p.split('id="retouch"', 1)[1].split(">", 1)[0] and 'action="/settings/retouch"' in form_
    assert p.index('id="retouch"') < p.index('id="fees-form"')
    assert "Light retouching (up to 5 favorites the client picks)" in p
    r = retouch(c, False)
    assert r.status_code == 302 and r.headers["Location"].endswith("#retouch")
    assert A.C.OFFER_RETOUCH is False
    p = c.get("/settings?tab=prices", environ_base=E).get_data(as_text=True)
    assert 'name="offer_retouch" value="1">Offer retouching' in p
    assert "Light retouching" not in p and "More retouching" not in p
    con = A.connect()
    assert json.loads(con.execute("SELECT value FROM settings WHERE key='rules'").fetchone()[0])["OFFER_RETOUCH"] is False
    assert "Turned off Offer retouching" in [r_[0] for r_ in con.execute("SELECT action FROM audit")]
    con.close()
    for t_ in ("family", "senior", "wedding"):
        steps = {st[:2] for v in A.C.WORKFLOW[t_].values() for st in v}
        assert ("auto", "favorites") not in steps and ("do", "Send retouched photos") not in steps
    retouch(c, True)
    assert A.C.OFFER_RETOUCH is True and ("do", "Send retouched photos", 28) in A.C.WORKFLOW["family"]["delivered"]

def test_without_retouching_the_agreement_promises_none(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "family", stage="booked")
    text_on = in_request(A, lambda: A.agreement_view(sid))["custom"]
    assert "light retouching on up to 5 photos you pick" in str(text_on)
    retouch(c, False)
    text_off = str(in_request(A, lambda: A.agreement_view(sid))["custom"])
    assert "retouching" not in text_off.lower()
    assert "Every image I deliver is edited as described on my price list." in text_off

def test_without_retouching_a_link_gallery_is_the_download_gallery_from_the_start(env):
    A, c, G, tmp, src, host = env
    retouch(c, False)
    sid = session(A, "family", delivery_method="link")
    p = page(c, sid)
    assert ">Deliver without picks</button>" not in p and "First the proof gallery goes online" not in p
    assert "Build download gallery from folder" in p
    build(c, sid, src)
    gid, = row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))
    assert G.is_download(gid)

def test_turning_retouching_off_drops_the_open_retouch_steps_but_keeps_retouching_owed(env):

    A, c, G, tmp, src, host = env
    nothing = session(A, "family", delivery_method="link", stage="delivered")
    owed = session(A, "family", delivery_method="link", stage="delivered", retouch_picks="Kitchen.jpg: brighten")
    for s_ in (nothing, owed):
        in_request(A, lambda: A.sync_workflow_tasks(s_))
        assert row(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send retouched photos' AND done IS NULL", (s_,))
    retouch(c, False)
    assert not row(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send retouched photos'", (nothing,))
    assert row(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send retouched photos' AND done IS NULL", (owed,))
    p = page(c, owed)
    assert "Retouch 1 photo" in p and "Upload the retouched finals on the Retouch row" in p
    assert "Retouch" not in page(c, nothing).split('id="needs"', 1)[1].split("</section>", 1)[0]

def test_offer_retouching_travels_in_the_settings_file(env):
    A, c, G, tmp, src, host = env
    import settings_io
    assert settings_io._rules({"OFFER_RETOUCH": False}) == {"OFFER_RETOUCH": False}
    try:
        settings_io._rules({"OFFER_RETOUCH": "no"})
        raise AssertionError("a word is not a switch")
    except settings_io.ImportProblem as e:
        assert "expected true or false" in str(e)
    retouch(c, False)
    assert A.C.current_rules()["OFFER_RETOUCH"] is False

def test_edit_details_sits_in_the_header_and_the_facts_near_the_top(env):
    A, c, G, tmp, src, host = env
    for stage in ("inquiry", "booked", "delivered", "closed"):
        sid = session(A, "family", delivery_method="usb", stage=stage, stops="Riverside Park")
        p = page(c, sid)
        head = p.split('<header class="ui-head"', 1)[1].split("</header>", 1)[0]
        assert f'<a class="ui-kick-edit ui-go" href="/session/{sid}/edit" id="edit-details">Edit details &rsaquo;</a>' in head, stage
        facts = p.split('id="session-facts"', 1)[1].split("</section>", 1)[0]

        for k_ in ("Date", "Place", "Files go", "Booked"):
            assert f'<span class="ui-fact-k">{k_}</span>' in facts, (stage, k_)
        assert "USB drive" in facts and "Riverside Park" in facts
        assert f'href="/session/{sid}/edit?from=details#f-shoot_date" aria-label="Change the date"' in facts

        assert 'data-ui-open="files-go" aria-controls="files-go" aria-label="Change how the files go"' in facts, stage
        want = "#f-delivery_method" if stage in ("delivered", "closed") else "#f-delivery_method-early"
        edit = c.get(f"/session/{sid}/edit", environ_base=E).get_data(as_text=True)
        assert 'id="f-shoot_date"' in edit and f'id="{want[1:]}"' in edit and edit.count('id="f-delivery_method"') == (1 if stage in ("delivered", "closed") else 0)
        assert "<span>Place" in edit
        assert 'href="#billing" aria-label="See the quote"' in facts

        assert ">Edit all session details</span></a>" in facts and 'id="more"' not in p

def test_every_step_on_the_list_can_be_closed_without_doing_it(env):

    A, c, G, tmp, src, host = env
    sid = session(A, "family", delivery_method="usb", stage="booked")
    in_request(A, lambda: A.sync_workflow_tasks(sid))
    c.post(f"/session/{sid}/quick", data={"act": "stage", "stage": "shot", "back": "/c/1"}, environ_base=E)
    needs = page(c, sid).split('id="needs"', 1)[1].split("</section>", 1)[0]
    rows_ = re.findall(r'<li class="ui-todo(?:\s[^"]*)?".*?</li>', needs, re.S)
    gal_ = [r_ for r_ in rows_ if ">Build gallery<" in r_]

    assert gal_ and 'name="step" value="gallery_built"' in gal_[0] and ">Skip</button>" in gal_[0]
    for r_ in rows_:
        assert ">Skip</button>" in r_ or "Collect" in r_ or "owes" in r_, r_[:300]
    c.post("/close", data={"sid": sid, "step": "gallery_built", "outcome": "na", "back": "/c/1"}, environ_base=E)
    needs = page(c, sid).split('id="needs"', 1)[1].split("</section>", 1)[0]
    assert not [r_ for r_ in re.findall(r'<li class="ui-todo"[^>]*>.*?</li>', needs, re.S) if ">Build gallery<" in r_]

def test_a_typed_gallery_date_never_claims_an_encrypted_gallery(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "family", delivery_method="usb", stage="delivered", gallery_built="2026-11-26")
    fold = page(c, sid).split('id="delivery"', 1)[1].split("</ul>", 1)[0]
    assert "Encrypted gallery built" not in fold and "Gallery step marked done" in fold
    build(c, sid, src)
    assert "Encrypted gallery built" in page(c, sid).split('id="delivery"', 1)[1].split("</ul>", 1)[0]

def test_without_retouching_the_quote_menu_sells_none(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "family", stage="inquiry")
    assert "Additional retouching, per image" in page(c, sid)
    retouch(c, False)
    assert "Additional retouching, per image" not in page(c, sid)
    with A.app.test_request_context():
        assert all("retouch" not in g_[1] for g_ in A.fee_groups())

def test_offer_retouching_is_found_as_other_switches_are(env):

    A, c, G, tmp, src, host = env
    p = c.get("/settings?tab=prices", environ_base=E).get_data(as_text=True)
    assert "price list, price list terms, retouching" in p
    found = c.get("/search?q=retouching", environ_base=E).get_data(as_text=True)
    assert "Offer retouching" in found and "f-offer_retouch" in found

def test_without_retouching_its_fee_is_hidden_and_its_price_kept(env):
    A, c, G, tmp, src, host = env
    def fees():
        con = A.connect()
        try:
            return json.loads(con.execute("SELECT items FROM fee_schedules ORDER BY id DESC LIMIT 1").fetchone()[0])
        finally:
            con.close()
    retouch(c, False)
    p = c.get("/settings?tab=prices", environ_base=E).get_data(as_text=True)
    assert "Additional retouching, per image" not in p
    form = {k_: v_ for k_, v_ in re.findall(r'name="(fee_(?:label|amt)_\d+)" value="([^"]*)"', p)}
    form.update({m_: "1" for m_ in re.findall(r'name="(fee_tax_\d+)" value="1" checked', p)})
    form.update(fees_posted="1", form_id="fees-form")
    c.post("/settings?tab=prices", data=form, environ_base=E)
    rt = [it for it in fees() if it["key"] == "retouch"]
    assert rt and rt[0]["amount"] == 20 and rt[0].get("taxable", True)
    sid = session(A, "family", stage="inquiry")
    r = c.post(f"/session/{sid}/quote/fee", data={"fee_key": "retouch", "qty": "1", "back": "/c/1"}, environ_base=E)
    con = A.connect()
    assert not con.execute("SELECT 1 FROM lines WHERE session_id=? AND fee_key='retouch'", (sid,)).fetchone()
    con.close()
    retouch(c, True)
    assert "Additional retouching, per image" in c.get("/settings?tab=prices", environ_base=E).get_data(as_text=True)

def test_a_drive_delivery_has_no_gallery_chores_until_a_gallery_is_asked_for(env):

    A, c, G, tmp, src, host = env
    retouch(c, False)
    sid = session(A, "family", delivery_method="usb", stage="booked")
    in_request(A, lambda: A.sync_workflow_tasks(sid))
    c.post(f"/session/{sid}/quick", data={"act": "stage", "stage": "shot", "back": "/c/1"}, environ_base=E)
    p = page(c, sid)
    needs = p.split('id="needs"', 1)[1].split("</section>", 1)[0]
    for w_ in (">Build gallery<", "QR letter", "Gallery opened"):
        assert w_ not in needs, w_
    assert "No gallery: the files go on a USB drive." in p and ">Share a gallery</button>" in p
    c.post(f"/session/{sid}/quick", data={"act": "stage", "stage": "delivered", "back": "/c/1"}, environ_base=E)
    p = page(c, sid)
    fold = p.split('id="delivery"', 1)[1].split("</ul>", 1)[0]
    assert "Gallery not built yet" not in fold and "QR letter" not in fold and "opened the gallery" not in fold
    assert "Final photos not delivered yet (USB drive)" in fold
    assert "Gallery opened" not in p.split('id="needs"', 1)[1].split("</section>", 1)[0]
    c.post(f"/session/{sid}/gallery/no-picks", environ_base=E)
    assert "Gallery not built yet" in page(c, sid).split('id="delivery"', 1)[1].split("</ul>", 1)[0]

    mid = session(A, "family", delivery_method="usb", stage="delivered", gallery_id="g_midflight01")
    sql(A, "INSERT INTO galleries (session_id, key, built_at) VALUES (?, 'k', '2026-11-20')", (mid,))
    assert "Gallery not built yet" in page(c, mid).split('id="delivery"', 1)[1].split("</ul>", 1)[0]

    retouch(c, True)
    other = session(A, "family", delivery_method="usb", stage="delivered")
    assert "Gallery not built yet" in page(c, other).split('id="delivery"', 1)[1].split("</ul>", 1)[0]

def test_the_new_inquiry_asks_how_files_are_delivered(env):
    A, c, G, tmp, src, host = env
    p = c.get("/inquiry?hid=1&type=family", environ_base=E).get_data(as_text=True)

    assert 'How will files be delivered? <span class="sub">(optional, pick any that apply)</span>' in p
    assert '<input type="radio" name="delivery_method" value="usb" checked>' in p
    assert '<input type="checkbox" name="deliver_gallery" value="1">Online gallery (download link)' in p
    once = re.search(r'name="once" value="([^"]+)"', p).group(1)
    c.post("/inquiry", data={"household_id": "1", "type": "family", "once": once, "delivery_method": "link"}, environ_base=E)
    con = A.connect()
    assert con.execute("SELECT delivery_method FROM sessions ORDER BY id DESC LIMIT 1").fetchone()[0] == "link"
    con.close()

def test_a_gallery_shared_after_a_usb_handover_waits_for_its_first_open(env):

    A, c, G, tmp, src, host = env
    from test_download_gallery import upload
    retouch(c, False)
    sid = session(A, "family", delivery_method="usb", stage="closed", sd_shipped="2026-11-20")
    c.post(f"/session/{sid}/gallery/no-picks", environ_base=E)
    p = page(c, sid)
    assert "The gallery asks for a codeword" not in p
    assert '<section class="ui-sec " id="gallery-sec"' in p
    upload(c, sid, "a.jpg", jpeg(), "export")
    c.post(f"/session/{sid}/gallery/build", data={"from": "uploads", "then_upload": "1"}, environ_base=E)
    join("gallery", sid)
    join("gal-up", sid)
    p = page(c, sid)
    assert "Not opened yet" in p and "Opened " not in p and "last checked" not in p.split('id="gallery"', 1)[1]
    assert "days left" not in p and "its 30 days start when the client first opens it" in p

def test_saving_after_a_change_link_returns_to_the_details_card(env):
    A, c, G, tmp, src, host = env
    sid = session(A, "family", delivery_method="usb", stage="delivered")
    edit = c.get(f"/session/{sid}/edit?from=details", environ_base=E).get_data(as_text=True)
    assert edit.count(f'<input type="hidden" name="back" value="/c/1?s={sid}#session-facts">') == 2
    r = c.post(f"/session/{sid}/delivery", data={"delivery_method": "sd", "back": f"/c/1?s={sid}#session-facts"}, environ_base=E)
    assert r.headers["Location"].endswith(f"/c/1?s={sid}#session-facts")
    assert f'name="back" value="/c/1?s={sid}#session-facts"' not in c.get(f"/session/{sid}/edit", environ_base=E).get_data(as_text=True)

def test_the_gallery_footer_keeps_the_phone_number_on_one_line():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "gallery_assets",
                            "viewer.html")).read()
    assert 'const sp = document.createElement("span"); sp.className = "nw"; sp.textContent = B.phone;' in src
    assert ".nw { white-space: nowrap; }" in src

def new_inquiry(A, c, **form):
    p = c.get("/inquiry?hid=1&type=family", environ_base=E).get_data(as_text=True)
    once = re.search(r'name="once" value="([^"]+)"', p).group(1)
    c.post("/inquiry", data=dict(household_id="1", type="family", once=once, **form), environ_base=E)
    con = A.connect()
    try:
        return con.execute("SELECT id FROM sessions ORDER BY id DESC LIMIT 1").fetchone()[0]
    finally:
        con.close()

def test_retouching_off_means_no_picking_round_for_a_usb_family_made_from_an_inquiry(env):

    A, c, G, tmp, src, host = env
    retouch(c, False)
    sid = new_inquiry(A, c, delivery_method="usb", shoot_date="2026-11-20")
    assert row(A, "SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0]
    with A.app.test_request_context():
        s_ = A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        assert not G_picking(A, s_)
    for stage in ("shot", "delivered"):
        c.post(f"/session/{sid}/quick", data={"act": "stage", "stage": stage, "back": "/c/1"}, environ_base=E)
        p = page(c, sid)
        needs = p.split('id="needs"', 1)[1].split("</section>", 1)[0]
        for w_ in (">Build gallery<", "QR letter", "Gallery opened", "Mail QR"):
            assert w_ not in needs, (stage, w_)
        fold = p.split('id="delivery"', 1)[1].split("</ul>", 1)[0] if 'id="delivery"' in p else ""
        assert "Gallery not built yet" not in fold and "QR letter" not in fold and "opened the gallery" not in fold
        assert "No mailing address" not in p
        assert "No gallery: the files go on a USB drive." in p

def G_picking(A, s_):
    steps = A.flow_for(s_["id"])
    return any(st[:2] == ("auto", "favorites") for v in steps.values() for st in v)

def test_a_check_on_one_client_never_shows_on_another_clients_page(env):

    A, c, G, tmp, src, host = env
    sql(A, "INSERT INTO households (id, name) VALUES (2, 'Other Family')")
    c.post("/client/1/check", data={"back": "/c/1"}, environ_base=E)
    import jobs
    assert jobs.JOBS.get("client-check:1", {}).get("state") == "done"
    other = c.get("/c/2", environ_base=E, follow_redirects=True).get_data(as_text=True)
    assert "Checking for anything new from the client" not in other and "Checked:" not in other
    assert '<aside class="jobs"' not in other
    assert "Checked:" not in c.get("/", environ_base=E, follow_redirects=True).get_data(as_text=True)

def test_an_online_gallery_is_promised_only_when_one_is_planned(env):
    A, c, G, tmp, src, host = env
    retouch(c, False)
    usb = new_inquiry(A, c, delivery_method="usb")
    link = new_inquiry(A, c, delivery_method="link")
    with A.app.test_request_context():
        A.db()
        assert A.session_delivery_text(usb) == "as full-resolution digital files on a USB drive"
        assert A.session_delivery_text(link).endswith("through a download link I send you, with an online gallery")
        s_ = A.q("SELECT * FROM sessions WHERE id=?", (usb,), one=True)
        terms = dict(A.quote_terms(s_, A.totals(usb)))
        assert terms["Delivery"].endswith("on a USB drive.") and "online gallery" not in terms["Delivery"]
        assert "online gallery" not in str(A.agreement_view(usb)["custom"])
    sql(A, "UPDATE sessions SET stage='delivered' WHERE id=?", (usb,))
    c.post(f"/session/{usb}/gallery/no-picks", environ_base=E)
    with A.app.test_request_context():
        A.db()
        assert A.session_delivery_text(usb).endswith("on a USB drive, with an online gallery")
    retouch(c, True)
    with A.app.test_request_context():
        A.db()
        assert "online gallery" not in A.session_delivery_text(new_inquiry(A, c, delivery_method="usb"))

def test_without_retouching_the_editing_terms_say_nothing_about_it(env):
    A, c, G, tmp, src, host = env
    retouch(c, False)
    assert "retouch" not in str(A.C.EDITING_POLICY).lower() and "retouch" not in str(A.C.build_editing("family")).lower()
