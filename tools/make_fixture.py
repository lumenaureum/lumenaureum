import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
YEAR = "2026"
SIDE_FILES = ("keyring.json", "deletions.json", "doc-numbers.json", "settings.json")

class Ctx:

    def __init__(self, A, c, work):
        self.A, self.c, self.work = A, c, work
        self.E = {"REMOTE_ADDR": "127.0.0.1"}

    def post(self, url, **data):
        r = self.c.post(url, data=data, environ_base=self.E)
        assert r.status_code in (200, 204, 302, 303), (url, r.status_code)
        return r

    def get(self, url):
        r = self.c.get(url, environ_base=self.E)
        assert r.status_code == 200, (url, r.status_code)
        return r

    def one(self, sql, args=()):
        con = self.A.connect()
        try:
            return con.execute(sql, args).fetchone()
        finally:
            con.close()

    def rows(self, sql, args=()):
        con = self.A.connect()
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def in_request(self, fn):
        A = self.A
        with A.app.test_request_context():
            A.db()
            out = fn()
            A.g.db.commit()
            return out

    def checkpoint(self):
        con = self.A.connect()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()

    def finances(self, year):

        A, seen = self.A, {}
        real = A.render_template

        def capture(name, **kw):
            if name == "money.html":
                seen.update(kw)
            return real(name, **kw)
        A.render_template = capture
        try:
            self.get(f"/money?year={year}")
        finally:
            A.render_template = real
        return dict(total=round(seen["total"], 2), refunds=round(seen["refunds"], 2), tax_due=round(seen["tax_due"], 2),
                    received={r["type"]: round(r["v"], 2) for r in seen["received"]},
                    by_month={r["mo"]: round(r["v"], 2) for r in seen["by_month"]},
                    outstanding={str(o["s"]["id"]): round(o["t"]["due"], 2) for o in seen["outstanding"]},
                    upcoming={str(o["s"]["id"]): round(o["t"]["due"], 2) for o in seen["upcoming"]})

def scenario_100(x, made_with):
    A, c, work, post, one, in_request = x.A, x.c, x.work, x.post, x.one, x.in_request
    E = x.E
    post("/setup/shoot", preset_families="1")
    post("/contact/new", contact_name="Avery Lane", household_name="Lane", email="avery@example.com")
    post("/contact/new", contact_name="Maria Ortiz", household_name="Ortiz", phone="555-0100")
    lane, ortiz = (one("SELECT id FROM households WHERE name=?", (n,))[0] for n in ("Lane", "Ortiz"))

    post(f"/client/{lane}/session", type="family", stage="inquiry", title="Lane family", shoot_date=f"{YEAR}-03-14")
    s_lane = one("SELECT MAX(id) FROM sessions")[0]
    fee = in_request(lambda: A.current_schedule()["items"][0]["key"])
    post(f"/session/{s_lane}/quote/fee", fee_key=fee, qty="1")
    post(f"/session/{s_lane}/money", what="line", label="Extra prints", amount="50")
    quoted0 = in_request(lambda: A.totals(s_lane))["quoted"]
    post(f"/session/{s_lane}/money", what="payment", kind="deposit", amount="100", method="check", received=f"{YEAR}-02-20")
    post(f"/session/{s_lane}/money", what="payment", kind="balance", amount=f"{quoted0 - 100:.2f}", method="card",
         received=f"{YEAR}-03-20")
    post(f"/session/{s_lane}/stage", stage="delivered")
    post(f"/session/{s_lane}/money", what="payment", kind="refund", amount="40", method="card", received=f"{YEAR}-04-02")
    assert c.get(f"/session/{s_lane}/invoice", environ_base=E).status_code == 200
    in_request(lambda: A._invoice_marked_sent(s_lane))

    post(f"/client/{ortiz}/session", type="family", stage="inquiry", title="Ortiz family", shoot_date=f"{YEAR}-06-06")
    s_ortiz = one("SELECT MAX(id) FROM sessions")[0]
    post(f"/session/{s_ortiz}/money", what="line", label="Family session", amount="250")
    post(f"/session/{s_ortiz}/money", what="payment", kind="deposit", amount="75", method="cash", received=f"{YEAR}-05-01")
    post(f"/session/{s_ortiz}/stage", stage="booked")
    post(f"/client/{ortiz}/session", type="family", stage="inquiry", title="Ortiz holiday")
    s_ortiz2 = one("SELECT MAX(id) FROM sessions")[0]

    post("/events", name="Spring Recital", date=f"{YEAR}-05-16", venue="Town Hall")
    eid = one("SELECT MAX(id) FROM events")[0]
    post(f"/event/{eid}/billing", billing="families")
    post(f"/event/{eid}/family", household_name="Bell", parent_name="Dana Bell", children="Grace 15")
    post(f"/event/{eid}/family", household_name="Chen", parent_name="Lee Chen", children="Mia 9 and Theo 7")
    post(f"/event/{eid}/family-price", amount="60")
    s_bell = one("SELECT s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.name='Bell'")[0]
    post(f"/session/{s_bell}/money", what="payment", kind="balance", amount="60", method="cash", received=f"{YEAR}-05-16")

    post("/contact/new", contact_name="Sam Doe", household_name="Doe")
    doe = one("SELECT MAX(id) FROM households")[0]
    post(f"/client/{doe}/session", type="family", stage="inquiry", title="Doe family")
    s_doe = one("SELECT MAX(id) FROM sessions")[0]
    x.checkpoint()
    shutil.copy(os.path.join(work, "crm.db"), os.path.join(work, "crm-before-delete.db"))
    post(f"/client/{doe}/delete", confirm="DELETE")
    assert not one("SELECT 1 FROM households WHERE id=?", (doe,))

    sessions = {}
    for (sid,) in A.connect().execute("SELECT id FROM sessions ORDER BY id").fetchall():
        t_ = in_request(lambda: A.totals(sid))
        refund_lines = one("SELECT COALESCE(SUM(amount), 0) FROM lines WHERE session_id=? AND fee_key LIKE 'refund:%'", (sid,))[0]
        sessions[str(sid)] = dict(quoted=round(t_["quoted"], 2), paid=round(t_["paid"], 2), due=round(t_["due"], 2),
                                  quoted_without_refund_lines=round(t_["quoted"] - refund_lines, 2),
                                  stage=one("SELECT stage FROM sessions WHERE id=?", (sid,))[0])
    finances = x.finances(YEAR)
    con = A.connect()
    expected = dict(
        made_with=made_with, password="test password 123", year=YEAR,
        schema_version=con.execute("PRAGMA user_version").fetchone()[0],
        clients={str(r[0]): r[1] for r in con.execute("SELECT id, name FROM households ORDER BY id")},
        sessions=sessions,
        payments={str(r[0]): dict(session=r[1], kind=r[2], amount=r[3], received=r[4])
                  for r in con.execute("SELECT id, session_id, kind, amount, received FROM payments ORDER BY id")},
        refund_lines={str(r[0]): dict(session=r[1], amount=r[2], fee_key=r[3])
                      for r in con.execute("SELECT id, session_id, amount, fee_key FROM lines WHERE fee_key LIKE 'refund:%'")},
        invoice_numbers={r[1]: r[0] for r in con.execute("SELECT session_id, number FROM doc_numbers ORDER BY number")},
        invoice_sent={str(r[0]): r[1] for r in con.execute("SELECT session_id, number FROM invoice_sent")},
        event=dict(id=eid, families=[r[0] for r in con.execute(
            "SELECT h.name FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.event_id=? ORDER BY h.name", (eid,))]),
        deleted=dict(client=doe, session=s_doe),
        sessions_by_name=dict(lane=s_lane, ortiz=s_ortiz, ortiz_inquiry=s_ortiz2, bell=s_bell),
        finances=finances,
    )
    con.close()
    return expected

CODEWORD = "amber harbor willow"
TIME_ZONE = "America/New_York"

def _wait_threads(prefix):
    for t in [t for t in threading.enumerate() if t.name.startswith(prefix)]:
        t.join(timeout=60)
        assert not t.is_alive(), f"{t.name} did not finish"

def scenario_101(x, made_with, more=None):

    A, work, post, get, one, rows, in_request = x.A, x.work, x.post, x.get, x.one, x.rows, x.in_request
    post("/settings/timezone", timezone=TIME_ZONE)
    post("/setup/shoot", **(more.shoot if more else dict(preset_families="1")))
    if more:
        more.setup(x)
    post("/contact/new", contact_name="Avery Lane", household_name="Lane", email="avery@example.com")
    post("/contact/new", contact_name="Maria Ortiz", household_name="Ortiz", phone="555-0100")
    lane, ortiz = (one("SELECT id FROM households WHERE name=?", (n,))[0] for n in ("Lane", "Ortiz"))

    post(f"/client/{lane}/session", type="family", stage="inquiry", title="Lane family", shoot_date=f"{YEAR}-03-14")
    s_lane = one("SELECT MAX(id) FROM sessions")[0]
    fee = in_request(lambda: A.current_schedule()["items"][0]["key"])
    post(f"/session/{s_lane}/quote/fee", fee_key=fee, qty="1")
    post(f"/session/{s_lane}/money", what="line", label="Extra prints", amount="50")
    quoted0 = in_request(lambda: A.totals(s_lane))["quoted"]
    post(f"/session/{s_lane}/money", what="payment", kind="deposit", amount="100", method="check", received=f"{YEAR}-02-20")
    get(f"/session/{s_lane}/invoice")
    assert not one("SELECT 1 FROM doc_numbers WHERE session_id=?", (s_lane,))
    post(f"/session/{s_lane}/invoice/issue")
    in_request(lambda: A._invoice_marked_sent(s_lane))
    post(f"/session/{s_lane}/money", what="payment", kind="balance", amount=f"{quoted0 - 100:.2f}", method="card",
         received=f"{YEAR}-03-20")
    post(f"/session/{s_lane}/stage", stage="delivered")
    post(f"/session/{s_lane}/money", what="payment", kind="refund", amount="40", method="card", received=f"{YEAR}-04-02")
    post(f"/session/{s_lane}/receipt/issue")

    from PIL import Image
    src = Path(work, "photos", "export", "lane")
    src.mkdir(parents=True)
    for i, col in enumerate(("red", "blue", "green"), 1):
        Image.new("RGB", (64, 48), col).save(src / f"img{i}.jpg", "JPEG")
    post(f"/session/{s_lane}/delivery", gallery_codeword=CODEWORD)
    assert one("SELECT gallery_codeword FROM sessions WHERE id=?", (s_lane,))[0] == CODEWORD
    post(f"/session/{s_lane}/gallery/build", source_dir=str(src))
    _wait_threads(f"gallery-{s_lane}")
    assert one("SELECT built_at FROM galleries WHERE session_id=?", (s_lane,))[0], "the gallery did not build"
    if more:
        more.gallery(x, s_lane)

    post(f"/client/{ortiz}/session", type="family", stage="inquiry", title="Ortiz family", shoot_date=f"{YEAR}-06-06")
    s_ortiz = one("SELECT MAX(id) FROM sessions")[0]
    post(f"/session/{s_ortiz}/money", what="line", label="Family session", amount="250")
    post(f"/session/{s_ortiz}/quote/send")
    post(f"/session/{s_ortiz}/money", what="payment", kind="deposit", amount="75", method="cash", received=f"{YEAR}-05-01")
    post(f"/session/{s_ortiz}/stage", stage="booked")
    get(f"/session/{s_ortiz}/invoice")
    post(f"/client/{ortiz}/session", type="family", stage="inquiry", title="Ortiz holiday")
    s_ortiz2 = one("SELECT MAX(id) FROM sessions")[0]
    post(f"/client/{ortiz}/session", type="family", stage="inquiry", title="Ortiz mini session")
    s_mini = one("SELECT MAX(id) FROM sessions")[0]
    post(f"/session/{s_mini}/money", what="line", label="Mini session", amount="150")
    post(f"/session/{s_mini}/quote/send")
    q_mini = one("SELECT MAX(id) FROM quote_versions WHERE session_id=?", (s_mini,))[0]
    post(f"/session/{s_mini}/delete")
    assert not one("SELECT 1 FROM sessions WHERE id=?", (s_mini,))

    post("/events", name="Spring Recital", date=f"{YEAR}-05-16", venue="Town Hall")
    eid = one("SELECT MAX(id) FROM events")[0]
    post(f"/event/{eid}/billing", billing="families")
    post(f"/event/{eid}/family", household_name="Bell", parent_name="Dana Bell", children="Grace 15")
    post(f"/event/{eid}/family", household_name="Chen", parent_name="Lee Chen", children="Mia 9 and Theo 7")
    post(f"/event/{eid}/family-price", amount="60")
    s_bell, s_chen = (one("SELECT s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.name=?", (n,))[0]
                      for n in ("Bell", "Chen"))
    post(f"/session/{s_bell}/money", what="payment", kind="balance", amount="60", method="cash", received=f"{YEAR}-05-16")
    get(f"/session/{s_bell}/receipt")
    post(f"/session/{s_chen}/invoice/issue")

    if more:
        more.data(x)

    post("/contact/new", contact_name="Sam Doe", household_name="Doe")
    doe = one("SELECT MAX(id) FROM households")[0]
    post(f"/client/{doe}/session", type="family", stage="inquiry", title="Doe family")
    s_doe = one("SELECT MAX(id) FROM sessions")[0]
    post(f"/client/{doe}/delete", confirm="DELETE")
    _wait_threads("job-client-delete")
    assert not one("SELECT 1 FROM households WHERE id=?", (doe,)), "the client delete did not finish"

    sessions = {}
    for (sid,) in rows("SELECT id FROM sessions ORDER BY id"):
        t_ = in_request(lambda: A.totals(sid))
        sessions[str(sid)] = dict(quoted=round(t_["quoted"], 2), paid=round(t_["paid"], 2), due=round(t_["due"], 2),
                                  refund_credit=round(t_.get("refund_credit", 0.0), 2),
                                  stage=one("SELECT stage FROM sessions WHERE id=?", (sid,))[0])
    bills = {}
    for sid in sorted(int(k) for k in sessions):
        for kind, receipt in (("invoice", False), ("receipt", True)):
            v = in_request(lambda: A.bill_ctx(sid, receipt=receipt))
            bills.setdefault(str(sid), {})[kind] = dict(number=v["number"], since=v.get("since", ""), paid=round(v["paid"], 2),
                                                        total=round(v["total"], 2), balance=round(v["balance"], 2))
    snapshots = {}
    for number, sid, kind, data in rows("SELECT number, session_id, kind, data FROM bill_snapshots ORDER BY number"):
        d_ = json.loads(data)
        snapshots[number] = dict(session=sid, kind=kind, paid=round(d_["paid"], 2), total=round(d_["total"], 2),
                                 balance=round(d_["balance"], 2), payments=len(d_["payments"]))
    deletions = json.loads(Path(work, "deletions.json").read_text())
    marks = json.loads(Path(work, "doc-numbers.json").read_text())
    gal = one("SELECT g.key, g.ekey, g.n_files, g.built_at, g.cw_fixed, s.gallery_id, s.gallery_built, s.gallery_codeword "
              "FROM galleries g JOIN sessions s ON s.id=g.session_id WHERE g.session_id=?", (s_lane,))
    link = in_request(lambda: A.app.config["gallery_link"](s_lane))
    finances = x.finances(YEAR)
    con = A.connect()
    expected = dict(
        made_with=made_with, password="test password 123", year=YEAR,
        schema_version=con.execute("PRAGMA user_version").fetchone()[0],
        clients={str(r[0]): r[1] for r in con.execute("SELECT id, name FROM households ORDER BY id")},
        sessions=sessions,
        payments={str(r[0]): dict(session=r[1], kind=r[2], amount=r[3], received=r[4])
                  for r in con.execute("SELECT id, session_id, kind, amount, received FROM payments ORDER BY id")},
        refund_credits={str(r[0]): dict(session=r[1], amount=r[2])
                        for r in con.execute("SELECT payment_id, session_id, amount FROM refund_credits ORDER BY payment_id")},
        doc_numbers={r[0]: dict(session=r[1], kind=r[2], issued=r[3])
                     for r in con.execute("SELECT number, session_id, kind, issued FROM doc_numbers ORDER BY number")},
        invoice_sent={str(r[0]): r[1] for r in con.execute("SELECT session_id, number FROM invoice_sent")},
        bills=bills, bill_snapshots=snapshots,
        quote_versions={str(r[0]): r[1] for r in con.execute("SELECT id, session_id FROM quote_versions ORDER BY id")},
        number_marks=marks,
        id_marks={r[0]: r[1] for r in con.execute("SELECT name, n FROM id_marks ORDER BY name")},
        side_keys=[r[0] for r in con.execute("SELECT name FROM side_keys ORDER BY name")],
        deletions=deletions,
        event=dict(id=eid, families=[r[0] for r in con.execute(
            "SELECT h.name FROM sessions s JOIN households h ON h.id=s.household_id WHERE s.event_id=? ORDER BY h.name", (eid,))]),
        deleted=dict(client=doe, session=s_doe, quoted_session=s_mini, quote=q_mini),
        sessions_by_name=dict(lane=s_lane, ortiz=s_ortiz, ortiz_inquiry=s_ortiz2, bell=s_bell, chen=s_chen),
        settings={r[0]: r[1] for r in con.execute("SELECT key, value FROM settings WHERE key IN ('timezone')")},
        gallery=dict(session=s_lane, gallery_id=gal[5], n_files=gal[2], built_at=gal[3], cw_fixed=gal[4],
                     session_built=gal[6], codeword=gal[7], has_codeword_key=bool(gal[1]) and gal[1] != gal[0],
                     link_asks_for_word=bool(link) and link.endswith("&c=1")),
        finances=finances,
    )
    con.close()
    if more:
        expected.update(more.measure(x, expected))
    return expected

LOOK = dict(gallery_font="elegant", gallery_bg="#262422", proof_mark="tiled", proof_mark_text="SAMPLE",
            proof_mark_strength="35")

def _png(color, size=(120, 48)):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()

def doc_text(html):

    import html as _h
    import re
    start, end = html.find("<h1>"), html.find("Just reply or call.")
    assert start >= 0 and end > start, "not an invoice or receipt page"
    body = re.sub(r"<[^>]+>", " ", html[start:end + len("Just reply or call.")])
    return " ".join(_h.unescape(body).split())

class More110:

    shoot = dict(preset_families="1", preset_headshots="1")

    def setup(self, x):
        post, work = x.post, x.work
        post("/setup/documents", rights_family="transfer", rights_headshot="license")
        host = Path(work, "host")
        host.mkdir()
        post("/setup/galleries", gallery_type="local", gallery_path=str(host), gallery_init="1",
             gallery_base_url="https://photos.example.com/g/", gallery_days="30", unopened_days="365")
        from vault import web as VW
        assert (VW.config().get("gallery_host") or {}).get("type") == "local", "the gallery host was not saved"
        r = x.c.post("/settings/logo", data={"logo": (__import__("io").BytesIO(_png("#8a5a6b")), "logo.png")},
                     environ_base=x.E, content_type="multipart/form-data")
        assert r.status_code in (302, 303)
        r = x.c.post("/settings/logo", data={"which": "dark", "look_posted": "1", **LOOK,
                                             "logo": (__import__("io").BytesIO(_png("#eee7dc")), "logo-dark.png")},
                     environ_base=x.E, content_type="multipart/form-data")
        assert r.status_code in (302, 303)
        post("/settings", look_posted="1", **LOOK)
        got = {k: x.one("SELECT value FROM settings WHERE key=?", (k,)) for k in LOOK}
        assert {k: v[0] if v else None for k, v in got.items()} == LOOK, got
        assert x.one("SELECT value FROM settings WHERE key='logo_dark_png'")[0]

    def gallery(self, x, s_lane):

        post, one = x.post, x.one
        from plugins import gallery as G
        post(f"/session/{s_lane}/gallery/upload")
        _wait_threads(f"gal-up-{s_lane}")
        assert one("SELECT upload_state FROM galleries WHERE session_id=?", (s_lane,))[0] == "up", \
            one("SELECT upload_state, upload_error FROM galleries WHERE session_id=?", (s_lane,))
        gid, ekey = one("SELECT s.gallery_id, g.ekey FROM sessions s JOIN galleries g ON g.session_id=s.id WHERE s.id=?",
                        (s_lane,))
        folder = Path(x.work, "host", gid)
        assert folder.is_dir(), "the gallery is not on the host"
        names = sorted(p.name for p in Path(x.work, "photos", "export", "lane").glob("*.jpg"))

        def sealed(picks, aad, at):
            import secrets as _s
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            iv = _s.token_bytes(12)
            return b"LAK1" + iv + AESGCM(G.key_bytes(ekey)).encrypt(
                iv, json.dumps({"v": 1, "at": at, "picks": picks}).encode(), aad)
        draft = [dict(i=0, name=names[0], r=True, note="soften the light", m=False, v=False),
                 dict(i=1, name=names[1], r=False, note="", m=True, v=False),
                 dict(i=2, name=names[2], r=False, note="", m=False, v=True)]
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = lambda hours: (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        (folder / "seen.txt").write_text(stamp(30)[:19] + "Z")
        (folder / "picks.bin").write_bytes(sealed(draft[:1], G.PICKS_AAD, stamp(26)))
        (folder / "draft.bin").write_bytes(sealed(draft, G.DRAFT_AAD, stamp(2)))
        post("/gallery/sync")
        n, at = one("SELECT draft_n, draft_at FROM galleries WHERE session_id=?", (s_lane,))
        assert n == 3 and at, (n, at)
        assert one("SELECT retouch_picks FROM sessions WHERE id=?", (s_lane,))[0]
        self.s_lane = s_lane

    def data(self, x):

        post, one = x.post, x.one
        post("/contact/new", contact_name="Jordan Price", household_name="Price", email="jordan@example.com")
        price = one("SELECT id FROM households WHERE name='Price'")[0]
        post(f"/client/{price}/session", type="headshot", stage="inquiry", title="Price headshots", shoot_date=f"{YEAR}-07-10")
        s_price = one("SELECT MAX(id) FROM sessions")[0]
        post(f"/session/{s_price}/money", what="line", label="Headshot session", amount="175")
        post(f"/session/{s_price}/quote/send")
        post(f"/session/{s_price}/stage", stage="booked")
        self.hint_before = x.in_request(lambda: x.A.booked_hint(s_price))
        post(f"/session/{s_price}/quick", act="sign", kind="agreement")
        x.get(f"/session/{s_price}/agreement")
        self.s_price = s_price

    def measure(self, x, expected):
        A, one, rows, in_request = x.A, x.one, x.rows, x.in_request
        import hashlib
        sids = [r[0] for r in rows("SELECT id FROM sessions ORDER BY id")]
        s_lane, s_price = self.s_lane, self.s_price
        gcols = ("key", "ekey", "n_files", "built_at", "cw_fixed", "upload_state", "uploaded_at", "removed_at",
                 "draft_n", "draft_at", "picks_hash", "limits_days")
        g = one(f"SELECT {', '.join(gcols)} FROM galleries WHERE session_id=?", (s_lane,))
        picks = one("SELECT first_visit, retouch_picks, favorites, veto FROM sessions WHERE id=?", (s_lane,))
        picking = in_request(lambda: [a_["what"] for fn in A.hooks.session_actions
                                      for a_ in fn(A.q("SELECT * FROM sessions WHERE id=?", (s_lane,), one=True)) or []])
        assert picking == [f"Client is still picking ({g[8]} photos marked)"], (picking, tuple(g))
        issued = {}
        for number, sid, kind in rows("SELECT number, session_id, kind FROM doc_numbers WHERE issued IS NOT NULL ORDER BY number"):
            issued[number] = doc_text(x.get(f"/session/{sid}/{'receipt' if kind == 'R' else 'invoice'}").get_data(as_text=True))
        settings = {r[0]: r[1] for r in rows("SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" * 7),
                                             tuple(LOOK) + ("timezone", "credit_off"))}
        logos = {k: hashlib.sha256((one("SELECT value FROM settings WHERE key=?", (k,)) or [""])[0].encode()).hexdigest()
                 for k in ("logo_png", "logo_dark_png")}
        return dict(
            settings=settings, logos=logos,
            license_types=sorted(A.C.LICENSE_TYPES),
            licensed={str(s_): bool(in_request(lambda: A.licensed(s_))) for s_ in sids},
            booked_hint={str(s_): in_request(lambda: A.booked_hint(s_)) for s_ in sids},
            booked_hint_before_signing=self.hint_before,
            gallery=dict(expected["gallery"], **{k: g[i] for i, k in enumerate(gcols)},
                         first_visit=picks[0], retouch_picks=picks[1], favorites=picks[2], veto=picks[3],
                         still_picking=picking),
            issued_text=issued,
            sessions_by_name=dict(expected["sessions_by_name"], price=s_price),
        )

def scenario_110(x, made_with):
    return scenario_101(x, made_with, more=More110())

class More111(More110):

    def gallery(self, x, s_lane):

        super().gallery(x, s_lane)
        post, one = x.post, x.one
        src = Path(x.work, "photos", "export", "lane")
        from PIL import Image
        Image.new("RGB", (64, 48), "orange").save(src / "img4.jpg", "JPEG")
        post(f"/session/{s_lane}/gallery/build", source_dir=str(src))
        _wait_threads(f"gallery-{s_lane}")
        _wait_threads(f"gal-up-{s_lane}")
        g = one("SELECT proof, prune_due, n_files, upload_state, upload_error FROM galleries WHERE session_id=?", (s_lane,))
        assert g[0] == 1 and g[1] and g[2] == 4 and g[3] == "up", tuple(g)

    def data(self, x):

        super().data(x)
        post, one, A = x.post, x.one, x.A
        post("/contact/new", contact_name="Robin Kim", household_name="Kim", email="robin@example.com")
        kim = one("SELECT id FROM households WHERE name='Kim'")[0]
        post(f"/client/{kim}/session", type="family", stage="inquiry", title="Kim family", shoot_date=f"{YEAR}-08-22")
        s_kim = one("SELECT MAX(id) FROM sessions")[0]
        post(f"/session/{s_kim}/money", what="line", label="Family session", amount="300")
        post(f"/session/{s_kim}/quote/send")
        x.get(f"/session/{s_kim}")
        assert "family" in A.C.NEEDS_AGREEMENT, sorted(A.C.NEEDS_AGREEMENT)
        assert one("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement'", (s_kim,)), "no agreement to sign"
        post(f"/session/{s_kim}/quick", act="sign", kind="agreement")
        assert one("SELECT stage FROM sessions WHERE id=?", (s_kim,))[0] in ("inquiry", "quoted")
        post(f"/session/{s_kim}/money", what="payment", kind="deposit", amount="100", method="check", received=f"{YEAR}-07-01")
        post(f"/session/{s_kim}/stage", stage="booked")
        post(f"/session/{s_kim}/stage", stage="shot")
        from PIL import Image
        src = Path(x.work, "photos", "export", "kim")
        src.mkdir(parents=True)
        for i, col in enumerate(("navy", "olive"), 1):
            Image.new("RGB", (64, 48), col).save(src / f"kim{i}.jpg", "JPEG")
        post(f"/session/{s_kim}/gallery/build", source_dir=str(src))
        _wait_threads(f"gallery-{s_kim}")
        assert x.in_request(lambda: A.release_blocked(s_kim)) > 0, "Kim should still owe the balance"
        post(f"/session/{s_kim}/gallery/upload")
        _wait_threads(f"gal-up-{s_kim}")
        g = one("SELECT proof, upload_state, upload_error FROM galleries WHERE session_id=?", (s_kim,))
        assert g[0] == 1 and g[1] == "up", tuple(g)
        self.s_kim = s_kim

    def measure(self, x, expected):
        out = super().measure(x, expected)
        A, one, rows, in_request = x.A, x.one, x.rows, x.in_request
        import hashlib
        sids = [r[0] for r in rows("SELECT id FROM sessions ORDER BY id")]
        for sid in sids:
            x.get(f"/session/{sid}")

        def due(sid):
            s = A.session_full(sid)
            return A.deposit_due(sid, s, A.priced_on(sid, s, A.totals(sid)["quoted"]))
        stages = {sid: one("SELECT stage FROM sessions WHERE id=?", (sid,))[0] for sid in sids}
        galleries = {}
        for r in rows("SELECT session_id, proof, prune_due, upload_state, n_files, built_at FROM galleries ORDER BY session_id"):
            galleries[str(r[0])] = dict(proof=r[1], prune_due=r[2], upload_state=r[3], n_files=r[4], built_at=r[5])
        return dict(
            out,
            sessions_by_name=dict(out["sessions_by_name"], kim=self.s_kim),
            snapshot_html={r[0]: hashlib.sha256(r[1].encode()).hexdigest()
                           for r in rows("SELECT number, html FROM bill_snapshots ORDER BY number")},
            priced_on={str(r[0]): r[1] for r in rows("SELECT id, priced_on FROM sessions ORDER BY id")},

            deposit_due={str(s_): in_request(lambda: due(s_)) for s_ in sids if stages[s_] not in ("inquiry", "quoted")},
            agreements={str(r[0]): [r[1], r[2]] for r in rows(
                "SELECT session_id, status, signed FROM documents WHERE kind='agreement' ORDER BY session_id, id")},
            booked_on={str(r[0]): r[1] for r in rows(
                "SELECT session_id, MIN(at) FROM stage_log WHERE stage='booked' GROUP BY session_id ORDER BY session_id")},
            galleries=galleries,
            proof_owed={str(self.s_kim): round(in_request(lambda: A.release_blocked(self.s_kim)), 2)},
            app_version=(one("SELECT value FROM settings WHERE key='app_version'") or [None])[0],
        )

def scenario_111(x, made_with):
    return scenario_101(x, made_with, more=More111())

WEBSITE = "https://studio.example.com"

def _noisy_jpeg(path, size, seed):

    import random
    from PIL import Image
    rnd = random.Random(seed)
    im = Image.new("RGB", size)
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(size[0] * size[1])])
    im.save(path, "JPEG", quality=90)

def doc_text_any(html):

    import html as _h
    import re
    return " ".join(_h.unescape(re.sub(r"<[^>]+>", " ", html)).split())

def _sealed(G, ekey, picks, aad, at):

    import secrets as _s
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = _s.token_bytes(12)
    return b"LAK1" + iv + AESGCM(G.key_bytes(ekey)).encrypt(iv, json.dumps({"v": 1, "at": at, "picks": picks}).encode(), aad)

class More120(More111):

    ZIP_PART = 16 * 1024
    HOST = "host"

    def setup(self, x):
        super().setup(x)

        x.A.app.view_functions["build_gallery"].__globals__["ZIP_PART"] = self.ZIP_PART
        x.post("/settings", business_website=WEBSITE)
        assert x.one("SELECT value FROM settings WHERE key='business_website'")[0] == WEBSITE

    def gallery(self, x, s_lane):

        More110.gallery(self, x, s_lane)
        src = Path(x.work, "photos", "export", "lane")
        from PIL import Image
        Image.new("RGB", (64, 48), "orange").save(src / "img4.jpg", "JPEG")
        self._build(x, s_lane, source_dir=str(src))
        g = x.one("SELECT proof, prune_due, n_files, upload_state, upload_error FROM galleries WHERE session_id=?", (s_lane,))
        assert g[0] == 1 and g[1] is None and g[2] == 4 and g[3] == "up", tuple(g)

    def _build(self, x, sid, **form):
        x.post(f"/session/{sid}/gallery/build", **form)
        _wait_threads(f"gallery-{sid}")
        _wait_threads(f"gal-up-{sid}")
        st = x.get(f"/session/{sid}/gallery/status").get_json()
        assert st["state"] == "done", st

    def _upload(self, x, sid):
        x.post(f"/session/{sid}/gallery/upload")
        _wait_threads(f"gal-up-{sid}")
        g = x.one("SELECT upload_state, upload_error FROM galleries WHERE session_id=?", (sid,))
        assert g[0] == "up", tuple(g)

    def _send_photo(self, x, sid, name, data, kind):

        st = x.c.get(f"/session/{sid}/photos/status?kind={kind}&size={len(data)}", headers={"X-File-Name": name},
                     environ_base=x.E).get_json()
        assert st.get("ok"), st
        at = st["received"]
        while at < len(data):
            r = x.c.post(f"/session/{sid}/photos/upload?kind={kind}&size={len(data)}&offset={at}",
                         data=data[at:at + st["piece"]], environ_base=x.E,
                         headers={"X-File-Name": name, "Content-Type": "application/octet-stream"})
            assert r.status_code == 200, r.get_data(as_text=True)
            at = r.get_json()["received"]

    def data(self, x):

        super().data(x)
        post, one, A = x.post, x.one, x.A
        from plugins import gallery as G
        from PIL import Image
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = lambda hours: (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        post("/contact/new", contact_name="Elena Reyes", household_name="Reyes", email="elena@example.com")
        reyes = one("SELECT id FROM households WHERE name='Reyes'")[0]
        post(f"/client/{reyes}/session", type="family", stage="inquiry", title="", shoot_date=f"{YEAR}-09-19")
        s_reyes = one("SELECT MAX(id) FROM sessions")[0]
        post(f"/session/{s_reyes}", title="Reyes autumn portraits", type="family", shoot_date=f"{YEAR}-09-19",
             delivery_method="link")
        assert tuple(one("SELECT stage, delivery_method FROM sessions WHERE id=?", (s_reyes,))) == ("inquiry", "link")
        post(f"/session/{s_reyes}/money", what="line", label="Family session", amount="400")
        post(f"/session/{s_reyes}/quote/send")
        x.get(f"/session/{s_reyes}")
        post(f"/session/{s_reyes}/quick", act="sign", kind="agreement")
        x.get(f"/session/{s_reyes}/agreement")
        post(f"/session/{s_reyes}/money", what="payment", kind="deposit", amount="150", method="card", received=f"{YEAR}-08-15")
        post(f"/session/{s_reyes}/stage", stage="booked")
        post(f"/session/{s_reyes}/stage", stage="shot")
        post(f"/session/{s_reyes}/money", what="payment", kind="balance", amount="250", method="card", received=f"{YEAR}-09-25")
        src = Path(x.work, "photos", "export", "reyes")
        src.mkdir(parents=True)
        for i, col in enumerate(("maroon", "teal", "gold"), 1):
            Image.new("RGB", (64, 48), col).save(src / f"reyes{i}.jpg", "JPEG")
        self._build(x, s_reyes, source_dir=str(src))
        gid = one("SELECT gallery_id FROM sessions WHERE id=?", (s_reyes,))[0]
        assert not G.is_download(gid) and one("SELECT proof FROM galleries WHERE session_id=?", (s_reyes,))[0] == 1
        self._upload(x, s_reyes)
        folder = Path(x.work, self.HOST, gid)
        ekey = one("SELECT ekey FROM galleries WHERE session_id=?", (s_reyes,))[0]
        picks = [dict(i=0, name="reyes1.jpg", r=True, note="warmer", m=False, v=False),
                 dict(i=2, name="reyes3.jpg", r=True, note="", m=True, v=False)]
        (folder / "seen.txt").write_text(stamp(50)[:19] + "Z")
        (folder / "picks.bin").write_bytes(_sealed(G, ekey, picks, G.PICKS_AAD, stamp(48)))
        post("/gallery/sync")
        assert one("SELECT retouch_picks FROM sessions WHERE id=?", (s_reyes,))[0], "the picks did not come in"
        assert "Retouched finals" in x.get(f"/c/{reyes}?s={s_reyes}").get_data(as_text=True)
        for i in (1, 2, 3):
            p_ = Path(x.work, f"final-{i}.jpg")
            _noisy_jpeg(p_, (160, 120), i)
            self._send_photo(x, s_reyes, p_.name, p_.read_bytes(), "final")
            p_.unlink()
        self._build(x, s_reyes, **{"from": "finals"})
        assert G.is_download(gid) and not G.online_download(gid)
        self._upload(x, s_reyes)
        assert G.online_download(gid) and not (folder / "seen.txt").exists()
        (folder / "seen.txt").write_text(stamp(20)[:19] + "Z")
        post("/gallery/sync")
        assert one("SELECT sd_shipped FROM sessions WHERE id=?", (s_reyes,))[0], "Files delivered was not counted"

        post("/contact/new", contact_name="Sam Diaz", household_name="Diaz", phone="555-0142")
        diaz = one("SELECT id FROM households WHERE name='Diaz'")[0]
        post(f"/client/{diaz}/session", type="family", stage="inquiry", title="", shoot_date=f"{YEAR}-10-03")
        s_diaz = one("SELECT MAX(id) FROM sessions")[0]
        post(f"/session/{s_diaz}/delivery", delivery_method="link")
        assert one("SELECT delivery_method FROM sessions WHERE id=?", (s_diaz,))[0] == "link"
        post(f"/session/{s_diaz}/money", what="line", label="Family session", amount="200")
        post(f"/session/{s_diaz}/money", what="payment", kind="balance", amount="200", method="cash", received=f"{YEAR}-10-03")
        post(f"/session/{s_diaz}/stage", stage="booked")
        post(f"/session/{s_diaz}/stage", stage="shot")
        src = Path(x.work, "photos", "export", "diaz")
        src.mkdir(parents=True)
        for i in (1, 2, 3, 4):
            _noisy_jpeg(src / f"diaz{i}.jpg", (160, 120), 10 + i)
        self._build(x, s_diaz, source_dir=str(src))
        self._upload(x, s_diaz)
        assert ">Deliver without picks</button>" in x.get(f"/c/{diaz}?s={s_diaz}").get_data(as_text=True)
        post(f"/session/{s_diaz}/gallery/no-picks")
        self._build(x, s_diaz, source_dir=str(src))
        gid_d = one("SELECT gallery_id FROM sessions WHERE id=?", (s_diaz,))[0]
        assert G.is_download(gid_d) and not G.online_download(gid_d)
        self._upload(x, s_diaz)
        assert G.online_download(gid_d)
        self.s_reyes, self.s_diaz = s_reyes, s_diaz

    def measure(self, x, expected):
        out = super().measure(x, expected)
        A, one, rows, in_request = x.A, x.one, x.rows, x.in_request
        import hashlib
        from plugins import gallery as G
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from vault import web as VW
        sids = [r[0] for r in rows("SELECT id FROM sessions ORDER BY id")]
        downloads = {}
        for sid in (self.s_reyes, self.s_diaz):
            gid = one("SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0]
            key, ekey = one("SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))
            host = Path(x.work, self.HOST, gid)
            blob = (host / "manifest.bin").read_bytes()
            m = json.loads(AESGCM(G.key_bytes(ekey or key)).decrypt(blob[:12], blob[12:], b"lumen-aureum-gallery:manifest.bin"))
            downloads[str(sid)] = dict(
                gallery_id=gid, is_download=G.is_download(gid), online_download=G.online_download(gid),
                dl=m.get("dl"), title=m.get("title"), site=(m.get("brand") or {}).get("site", m.get("site")),
                items=[dict(n=it["n"], on=it["on"], o=it["o"], os=it["os"]) for it in m["items"]],
                zips=[dict(name=z["name"], size=z["size"], parts=z["parts"]) for z in m["zips"]],
                manifest_sha256=hashlib.sha256(blob).hexdigest(),
                host_files=sorted(p.name for p in host.iterdir()),
                built_files=sorted(p.name for p in Path(x.work, "galleries", gid).iterdir()))
            assert downloads[str(sid)]["dl"] == 1 and len(downloads[str(sid)]["zips"][0]["parts"]) > 1, downloads[str(sid)]

        def steps(sid):
            return {st_["label"]: bool(st_["done"]) for stage in A.workflow(sid)["stages"] for st_ in stage["steps"]}
        delivered = {}
        for sid in (self.s_reyes, self.s_diaz):
            sd, fv = one("SELECT sd_shipped, first_visit FROM sessions WHERE id=?", (sid,))
            s_ = in_request(lambda: A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True))
            exp = in_request(lambda: A.gallery_expires(A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)))
            delivered[str(sid)] = dict(sd_shipped=sd, first_visit=fv, files_delivered=in_request(lambda: steps(sid))["Files delivered"],
                                       expires=exp.isoformat() if exp else None, stage=s_["stage"])
        assert delivered[str(self.s_reyes)]["files_delivered"] and not delivered[str(self.s_diaz)]["sd_shipped"]
        brands = {}
        for (sid,) in rows("SELECT session_id FROM galleries ORDER BY session_id"):
            b_ = in_request(lambda: A.app.config["gallery_brand"](A.q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)))
            brands[str(sid)] = dict(client=b_["client"], site=b_["site"])
        finals = {str(r[0]): r[1] for r in rows(
            "SELECT session_id, group_concat(name, ',') FROM (SELECT session_id, name FROM photo_files WHERE kind='final'"
            " AND state='done' ORDER BY session_id, name) GROUP BY session_id")}
        host_cfg = {k: v for k, v in (VW.config().get("gallery_host") or {}).items() if k != "path"}
        import re
        words = doc_text_any(x.get(f"/session/{self.s_reyes}/agreement").get_data(as_text=True))
        link_clause = re.search(r"The link stays open for \d+ days[^.]*\.", words)
        assert link_clause, "the agreement does not say how long the link stays open"
        return dict(
            out,
            sessions_by_name=dict(out["sessions_by_name"], reyes=self.s_reyes, diaz=self.s_diaz),
            delivery_method={str(r[0]): r[1] for r in rows("SELECT id, delivery_method FROM sessions ORDER BY id")},
            delivery_history={str(r[0]): r[1] for r in rows(
                "SELECT session_id, group_concat(action, ' | ') FROM (SELECT session_id, action FROM audit"
                " WHERE action LIKE 'Delivery changed%' ORDER BY id) GROUP BY session_id")},
            no_picks=[r[0] for r in rows("SELECT DISTINCT session_id FROM audit WHERE action=? ORDER BY session_id",
                                         (G.NO_PICKS_ACTION,))],
            download_galleries=downloads, delivered=delivered, gallery_brand=brands, finals=finals,
            signed_delivery={str(sid): in_request(lambda: A.signed_delivery_differs(sid)) for sid in sids},
            website=(one("SELECT value FROM settings WHERE key='business_website'") or [None])[0],
            gallery_host=host_cfg, gallery_base_url=one("SELECT value FROM settings WHERE key='gallery_base_url'")[0],
            link_clause=link_clause.group(0),
            picks={str(r[0]): dict(retouch_picks=r[1], favorites=r[2], veto=r[3]) for r in rows(
                "SELECT id, retouch_picks, favorites, veto FROM sessions WHERE id IN (?, ?) ORDER BY id",
                (self.s_reyes, self.s_diaz))},
            storage_keys=sorted(VW.config()),
            zip_part=A.app.view_functions["build_gallery"].__globals__["ZIP_PART"],
        )

def scenario_120(x, made_with):
    return scenario_101(x, made_with, more=More120())

STUDIO = dict(business_name="Lark & Pine Studio", photographer_name="Avery Marsh", business_email="hello@studio.example")
SITE = dict(site_name="Lark & Pine Studio", site_tagline="Family portraits by the river",
            site_intro="Unhurried sessions, natural light.", site_description="Family and headshot photography.",
            site_about="I photograph families as they are.\nSessions run about an hour.", site_inquiries="1")
INQUIRIES = (dict(v=1, name="Ada Lovelace", email="ada@example.org", phone="", type="family", date=f"{YEAR}-12-05",
                  message="Family photos before the holidays?\nThree of us.", hp="", t=16000),
             dict(v=1, name="Grace Hopper", email="grace@example.org", phone="555-0177", type="family", date="",
                  message="Do you travel to the coast?", hp="", t=21000))

class More130(More120):

    HOST = "web/g"
    EARLIER = "host"
    BASE = "https://photos.example.com/g/"

    def gallery(self, x, s_lane):

        super().gallery(x, s_lane)
        post, one = x.post, x.one
        from plugins import gallery as G
        from vault import web as VW
        new = Path(x.work, self.HOST)
        new.mkdir(parents=True)
        post("/setup/galleries", gallery_type="local", gallery_path=str(new), gallery_init="1",
             gallery_base_url=self.BASE, gallery_days="30", unopened_days="365")
        cfg = VW.config()
        assert cfg["gallery_host"]["path"] == str(new), cfg["gallery_host"]
        assert [h["path"] for h in G.earlier_host_cfgs(cfg)] == [str(Path(x.work, self.EARLIER))], cfg
        assert "Lane: online" in x.get("/galleries/move").get_data(as_text=True)
        post("/galleries/move")
        gid = one("SELECT gallery_id FROM sessions WHERE id=?", (s_lane,))[0]
        names = {p.name for p in (new / gid).iterdir()}
        assert {"index.html", "manifest.bin", "limits.txt", "picks.bin", "draft.bin"} <= names, names
        assert (Path(x.work, self.EARLIER, gid) / "manifest.bin").exists()
        fp, moved = one("SELECT host_fp, moved_from FROM galleries WHERE session_id=?", (s_lane,))
        assert fp == G.fingerprint_of(cfg["gallery_host"]) and moved == G.fingerprint_of(G.earlier_host_cfgs(cfg)[0])
        assert "Put 1 gallery" not in x.get("/galleries/move").get_data(as_text=True)

    def _client(self, x, name, contact, email, title, shoot, amount):
        x.post("/contact/new", contact_name=contact, household_name=name, email=email)
        hid = x.one("SELECT id FROM households WHERE name=?", (name,))[0]
        x.post(f"/client/{hid}/session", type="family", stage="inquiry", title=title, shoot_date=shoot)
        sid = x.one("SELECT MAX(id) FROM sessions")[0]
        x.post(f"/session/{sid}/money", what="line", label="Family session", amount=str(amount))
        return hid, sid

    def _pay(self, x, sid, kind, amount, day, method="card"):
        x.post(f"/session/{sid}/money", what="payment", kind=kind, amount=f"{amount:.2f}", method=method, received=day)

    def _due(self, x, sid):
        return round(x.in_request(lambda: x.A.totals(sid))["due"], 2)

    def data(self, x):
        super().data(x)
        post, one, A = x.post, x.one, x.A
        from plugins import gallery as G
        from datetime import datetime, timedelta, timezone
        post("/settings", **STUDIO)
        post("/setup/tax", tax_mode="flat", sales_tax_pct="6")
        assert x.in_request(lambda: A.sales_tax_pct()) == 6.0

        _, s_ellis = self._client(x, "Ellis", "Jamie Ellis", "jamie@example.com", "Ellis family", f"{YEAR}-10-04", 300)
        assert round(x.in_request(lambda: A.totals(s_ellis))["quoted"], 2) == 318.00
        self._pay(x, s_ellis, "deposit", 100, f"{YEAR}-09-05")
        post(f"/session/{s_ellis}/stage", stage="booked")
        post(f"/session/{s_ellis}/invoice/issue")
        post(f"/session/{s_ellis}/stage", stage="shot")
        self._pay(x, s_ellis, "balance", self._due(x, s_ellis), f"{YEAR}-10-10")
        self._pay(x, s_ellis, "tip", 50, f"{YEAR}-10-10", "cash")
        post(f"/session/{s_ellis}/money", what="payment", kind="tip_back", amount="10.00", method="cash",
             received=f"{YEAR}-10-12")
        post(f"/session/{s_ellis}/receipt/issue")
        t_ = x.in_request(lambda: A.totals(s_ellis))
        assert round(t_["tips"], 2) == 40.00 and round(t_["due"], 2) == 0.00, dict(t_)

        _, s_foster = self._client(x, "Foster", "Morgan Foster", "morgan@example.com", "Foster family", f"{YEAR}-09-20", 200)
        self._pay(x, s_foster, "deposit", 50, f"{YEAR}-09-12")
        post(f"/session/{s_foster}/stage", stage="booked")
        post(f"/session/{s_foster}/stage", stage="shot")
        post("/close", sid=str(s_foster), step="balance_paid", outcome="waived")
        assert one("SELECT amount FROM write_offs WHERE session_id=?", (s_foster,))[0] == 162.00

        _, s_grant = self._client(x, "Grant", "Riley Grant", "riley@example.com", "Grant family", f"{YEAR}-11-07", 250)
        self._pay(x, s_grant, "deposit", 75, f"{YEAR}-08-01")
        post(f"/session/{s_grant}/stage", stage="booked")
        self._pay(x, s_grant, "balance", 100, f"{YEAR}-09-01")
        post(f"/session/{s_grant}/stage", stage="lost", lost_reason="Canceled")
        t_ = x.in_request(lambda: A.totals(s_grant))
        assert t_["kept"] > 0.005 and t_["refund_due"] > 0.005, dict(t_)
        self._pay(x, s_grant, "refund", round(t_["refund_due"], 2), f"{YEAR}-10-20")

        h_jensen, s_jensen = self._client(x, "Jensen", "Kai Jensen", "kai@example.com", "Jensen family", f"{YEAR}-07-11", 120)
        post(f"/session/{s_jensen}/stage", stage="booked")
        self._pay(x, s_jensen, "balance", self._due(x, s_jensen), f"{YEAR}-07-15")
        self._pay(x, s_jensen, "tip", 20, f"{YEAR}-07-15", "cash")
        post(f"/client/{h_jensen}/delete", confirm="DELETE")
        _wait_threads("job-client-delete")
        assert not one("SELECT 1 FROM households WHERE id=?", (h_jensen,)), "the client delete did not finish"
        assert one("SELECT COUNT(*) FROM books WHERE kind='tip'")[0] == 1

        from PIL import Image
        now = datetime.now(timezone.utc).replace(microsecond=0)
        gone = {}
        for name, contact, cols in (("Hale", "Quinn Hale", ("plum", "khaki")), ("Ivers", "Sasha Ivers", ("coral", "slategray"))):
            _, sid = self._client(x, name, contact, f"{name.lower()}@example.com", f"{name} family", f"{YEAR}-09-26", 150)
            post(f"/session/{sid}/stage", stage="booked")
            self._pay(x, sid, "balance", self._due(x, sid), f"{YEAR}-09-26")
            post(f"/session/{sid}/stage", stage="shot")
            src = Path(x.work, "photos", "export", name.lower())
            src.mkdir(parents=True)
            for i, col in enumerate(cols, 1):
                Image.new("RGB", (64, 48), col).save(src / f"{name.lower()}{i}.jpg", "JPEG")
            self._build(x, sid, source_dir=str(src))
            self._upload(x, sid)
            gid = one("SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0]
            (Path(x.work, self.HOST, gid) / "seen.txt").write_text((now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            gone[name] = (sid, gid)
        post("/gallery/sync")
        post("/gallery/sync")
        for name, (sid, gid) in gone.items():
            g = one("SELECT upload_state, removed_at, dark_at FROM galleries WHERE session_id=?", (sid,))
            assert g[0] == "removed" and g[1] and g[2], (name, tuple(g))
            assert [p.name for p in Path(x.work, self.HOST, gid).iterdir()] == ["index.html"], name
        s_hale, s_ivers = gone["Hale"][0], gone["Ivers"][0]
        post(f"/session/{s_ivers}/gallery/back-online", days="14")
        _wait_threads(f"gal-up-{s_ivers}")
        g = one("SELECT upload_state, removed_at, dark_at, restored_at, restore_days FROM galleries WHERE session_id=?", (s_ivers,))
        assert g[0] == "up" and g[3] and g[4] == 14, tuple(g)
        assert (Path(x.work, self.HOST, gone["Ivers"][1]) / "manifest.bin").exists()
        for sid in (s_hale, s_ivers, s_ellis):
            x.get(f"/session/{sid}")

        from plugins import studio_page as S
        post("/settings/studio-page/switch", use="1")
        post("/settings/studio-page/save", **SITE)
        import io
        r = x.c.post("/settings/studio-page/photos/add", data={"photos": [(io.BytesIO(_jpeg("#4a6b5d")), "river.jpg")]},
                     content_type="multipart/form-data", environ_base=x.E)
        assert r.status_code in (302, 303)
        assert one("SELECT COUNT(*) FROM studio_photos")[0] == 1
        post("/settings/studio-page/upload")
        _wait_threads("studio-page")
        state = one("SELECT value FROM settings WHERE key='site_state'")
        assert state and state[0] == "up", (state, one("SELECT value FROM settings WHERE key='site_error'"))
        web = Path(x.work, self.HOST).parent
        assert (web / "index.html").is_file() and (web / "inquiry.php").is_file()
        pub = S.inquiry_public(one("SELECT value FROM settings WHERE key='site_inquiry_key'")[0])
        for i, f_ in enumerate(INQUIRIES):
            (web / "inquiries" / f"{i:016x}-{os.urandom(16).hex()}.bin").write_bytes(S.seal_inquiry(pub, f_))
        post("/settings/studio-page/inquiries")
        _wait_threads("studio-page-inquiries")
        assert one("SELECT COUNT(*) FROM studio_inquiry_review")[0] == 2
        rid = one("SELECT id FROM studio_inquiry_review WHERE fields LIKE '%Ada Lovelace%'")[0]
        post("/settings/studio-page/inquiries/review", do="add", id=str(rid))
        assert one("SELECT COUNT(*) FROM studio_inquiry_review")[0] == 1
        s_ada = one("SELECT s.id FROM sessions s JOIN households h ON h.id=s.household_id WHERE h.name='Ada Lovelace'")[0]

        r = x.c.post("/security/master-key", data={"secret": x.c.master_secret}, environ_base=x.E)
        assert "That's your master key" in r.get_data(as_text=True)
        self.s130 = dict(ellis=s_ellis, foster=s_foster, grant=s_grant, jensen=s_jensen, hale=s_hale, ivers=s_ivers,
                         ada=s_ada)
        self.h_jensen = h_jensen

    def measure(self, x, expected):
        out = super().measure(x, expected)
        A, one, rows, in_request = x.A, x.one, x.rows, x.in_request
        import csv
        import hashlib
        import io
        import zipfile
        from plugins import gallery as G
        from vault import web as VW
        r2 = lambda v: round(v or 0.0, 2)
        sids = [r[0] for r in rows("SELECT id FROM sessions ORDER BY id")]
        hids = [r[0] for r in rows("SELECT id FROM households ORDER BY id")]
        money = {}
        for sid in sids:
            t_ = in_request(lambda: A.totals(sid))
            money[str(sid)] = {k: r2(t_[k]) for k in ("quoted", "paid", "due", "tips", "kept", "refund_due",
                                                       "refund_credit", "written_off")}
        ym = in_request(lambda: A.year_money(YEAR))
        year = dict(total=r2(ym["total"]), refunds=r2(ym["refunds"]), tax_due=r2(ym["tax_due"]),
                    tax_adjusted=r2(ym["tax_adjusted"]), written_off=r2(ym["written_off"]), tips=r2(ym["tips"]),
                    by_month={r["mo"]: r2(r["v"]) for r in ym["by_month"]},
                    tax_rows=[dict(kind=r["kind"], day=r["day"], amount=r2(r["amount"]), tax=r2(r["tax"]),
                                   taxable=r2(r["taxable"])) for r in ym["tax_rows"]])
        statements = {}
        for hid in hids:
            for m in range(1, 13):
                v = in_request(lambda: A.statement_ctx(hid, f"{YEAR}-{m:02d}"))
                statements[f"{hid} {YEAR}-{m:02d}"] = dict(
                    opening=r2(v["opening"]), closing=r2(v["closing"]),
                    invoices=[[i["date"], i["number"], r2(i["amount"])] for i in v["invoices"]],
                    payments=[[p["date"], p["kind"], r2(p["amount"])] for p in v["payments"]])
        z = zipfile.ZipFile(io.BytesIO(x.get(f"/money/accounts.zip?year={YEAR}").data))
        export = {n: list(csv.reader(io.StringIO(z.read(n).decode("utf-8-sig")))) for n in sorted(z.namelist())}
        gcols = ("upload_state", "uploaded_at", "removed_at", "dark_at", "restored_at", "restore_days", "restore_once",
                 "host_fp", "link_base", "moved_from", "limits_contact", "limits_days", "n_files", "proof", "built_at")
        galleries = {str(r[0]): dict(zip(gcols, r[1:])) for r in rows(
            f"SELECT session_id, {', '.join(gcols)} FROM galleries ORDER BY session_id")}
        cfg = VW.config()
        here = os.path.realpath(x.work)
        on_host = {}
        for sid, gid in rows("SELECT s.id, s.gallery_id FROM galleries g JOIN sessions s ON s.id=g.session_id ORDER BY s.id"):
            on_host[str(sid)] = {where: sorted(p.name for p in Path(x.work, where, gid).iterdir()) if Path(x.work, where, gid).is_dir()
                                 else None for where in (self.HOST, self.EARLIER)}
        dark = self.s130["hale"]
        dark_gid = one("SELECT gallery_id FROM sessions WHERE id=?", (dark,))[0]
        web = Path(x.work, self.HOST).parent
        site_settings = {r[0]: r[1] for r in rows("SELECT key, value FROM settings WHERE key LIKE 'site\\_%' ESCAPE '\\'"
                                                   " AND key NOT IN ('site_inquiry_key') ORDER BY key")}
        return dict(
            out,
            sessions_by_name=dict(out["sessions_by_name"], **{k: v for k, v in self.s130.items() if k != "jensen"}),
            deleted_130=dict(client=self.h_jensen, session=self.s130["jensen"]),
            data_folder=here,
            hosts=dict(current=os.path.relpath(cfg["gallery_host"]["path"], x.work),
                       earlier=[os.path.relpath(h["path"], x.work) for h in G.earlier_host_cfgs(cfg)],
                       current_fp=G.fingerprint_of(cfg["gallery_host"]),
                       earlier_fp=[G.fingerprint_of(h) for h in G.earlier_host_cfgs(cfg)]),
            storage_keys=sorted(cfg),
            money=money, year_money=year, statements=statements, export=export,
            payments_kept={str(r[0]): dict(session=r[1], kind=r[2], amount=r[3], received=r[4], tax=r[5], taxable=r[6],
                                           voided=r[7]) for r in rows(
                "SELECT id, session_id, kind, amount, received, tax, taxable, voided FROM payments ORDER BY id")},
            books=[list(r) for r in rows("SELECT id, received, amount, tax, type, kind FROM books ORDER BY id")],
            write_offs=[list(r) for r in rows("SELECT id, session_id, amount, at, why FROM write_offs ORDER BY id")],
            tax_adjustments=[list(r) for r in rows(
                "SELECT id, session_id, at, tax, taxable, why FROM tax_adjustments ORDER BY id")],
            charges=[list(r) for r in rows("SELECT id, session_id, at, amount, number, label FROM charges ORDER BY id")],
            lost={str(r[0]): r[1] for r in rows("SELECT id, lost_reason FROM sessions WHERE stage='lost' ORDER BY id")},
            tax_rates={str(r[0]): r[1] for r in rows("SELECT id, tax_rate FROM sessions ORDER BY id")},
            galleries_130=galleries, on_host=on_host,
            leftovers=[list(r) for r in rows("SELECT gallery_id, host_fp, added FROM gallery_leftovers ORDER BY gallery_id")],
            dark_page_sha256=hashlib.sha256((Path(x.work, self.HOST, dark_gid) / "index.html").read_bytes()).hexdigest(),
            business={k: (one("SELECT value FROM settings WHERE key=?", (k,)) or [None])[0] for k in STUDIO},
            sales_tax={k: (one("SELECT value FROM settings WHERE key=?", (k,)) or [None])[0] for k in ("tax_mode", "sales_tax_pct")},
            site_settings=site_settings,
            site_key_sha256=hashlib.sha256(one("SELECT value FROM settings WHERE key='site_inquiry_key'")[0].encode()).hexdigest(),
            studio_photos=[dict(id=r[0], source=r[1], name=r[2], data_sha256=hashlib.sha256(r[3]).hexdigest() if r[3] else None)
                           for r in rows("SELECT id, source, name, data FROM studio_photos ORDER BY sort, id")],
            studio_files=[list(r) for r in rows("SELECT name, host_fp, size, sha256 FROM studio_files ORDER BY name")],
            site_on_host=sorted(str(p.relative_to(web)) for p in web.rglob("*") if p.is_file()
                                and Path(self.HOST).name not in p.relative_to(web).parts[:1]),
            studio_inquiries=[list(r) for r in rows(
                "SELECT sha256, session_id, household_id, outcome FROM studio_inquiries ORDER BY sha256")],
            inquiry_review=[json.loads(r[0]) for r in rows("SELECT fields FROM studio_inquiry_review ORDER BY id")],
            inquiry_added=dict(zip(("household", "name", "source", "type", "stage", "shoot_date", "date_tentative"), one(
                """SELECT h.id, h.name, h.source, s.type, s.stage, s.shoot_date, s.date_tentative FROM sessions s
                   JOIN households h ON h.id=s.household_id WHERE s.id=?""", (self.s130["ada"],)))),
            master_checked=(one("SELECT value FROM settings WHERE key='master_checked'") or [None])[0],
        )

def _jpeg(color, size=(400, 300)):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=90)
    return buf.getvalue()

def scenario_130(x, made_with):
    return scenario_101(x, made_with, more=More130())

VERSIONS = {
    "1.0.0": dict(commit="a0966c9", scenario=scenario_100, files=("crm-before-delete.db",), env={}),
    "1.0.1": dict(commit="v1.0.1", scenario=scenario_101, files=(),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),
    "1.1.0": dict(commit="v1.1.0", scenario=scenario_110, files=(),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.1.1": dict(commit="9e0e799", scenario=scenario_111, files=(),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.2.0": dict(commit="e37c6e6", scenario=scenario_120, files=(), dirs=("galleries", "host", "photos"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.3.0": dict(commit="58a774b", scenario=scenario_130, files=(), dirs=("galleries", "galleries-kept", "host", "web", "photos"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),
}

def inner(version, code, work, made_with):

    sys.path[:0] = [code, os.path.join(code, "tests")]
    import flask
    from helpers import HttpsClient, signed_in_client
    flask.Flask.test_client_class = HttpsClient
    import app as A
    c = signed_in_client(A)
    x = Ctx(A, c, work)
    expected = VERSIONS[version]["scenario"](x, made_with)
    x.checkpoint()
    Path(work, "expected.json").write_text(json.dumps(expected, indent=1, sort_keys=True) + "\n")

def main(argv):
    if argv[:1] == ["--inner"]:
        return inner(*argv[1:5])
    args = dict(zip(argv[::2], argv[1::2]))
    version = args.get("--version")
    if version not in VERSIONS:
        sys.exit(f"--version must be one of {', '.join(VERSIONS)}")
    spec = VERSIONS[version]
    out = Path(args.get("--out", REPO / "tests" / "fixtures" / f"db-{version}"))
    short = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=7", spec["commit"] + "^{commit}"], check=True,
                           capture_output=True, text=True).stdout.strip()
    made_with = f"{version} ({short})"
    code, made = args.get("--code"), None
    if not code:
        made = code = tempfile.mkdtemp(prefix=f"lumenaureum-{version.replace('.', '')}-")
        os.rmdir(code)
        subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", code, short], check=True)
    try:
        with tempfile.TemporaryDirectory() as work:
            env = dict(os.environ, CRM_TESTING="1", CRM_DB=os.path.join(work, "crm.db"),
                       CRM_STORAGE_CFG=os.path.join(work, "storage.json"), CRM_PHOTOS_ROOT=os.path.join(work, "photos"))
            env.update({k: (os.path.join(work, v) if k == "CRM_GALLERY_ROOT" else v) for k, v in spec["env"].items()})
            env.pop("CRM_KEYRING", None)
            subprocess.run([sys.executable, __file__, "--inner", version, str(Path(code).resolve()), work, made_with],
                           check=True, env=env, cwd=work)
            out.mkdir(parents=True, exist_ok=True)
            for name in ("crm.db", "expected.json") + spec["files"] + SIDE_FILES:
                if os.path.exists(os.path.join(work, name)):
                    shutil.copy(os.path.join(work, name), out / name)
                    print("wrote", out / name)
            for name in spec.get("dirs", ()):
                if os.path.isdir(os.path.join(work, name)):
                    shutil.rmtree(out / name, ignore_errors=True)
                    shutil.copytree(os.path.join(work, name), out / name)
                    print("wrote", out / name)
            for extra in sorted(os.listdir(work)):
                print("  (in the data folder:", extra + ")")
    finally:
        if made:
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", made], check=False)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
