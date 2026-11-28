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
        self.v15 = False

    def post(self, url, **data):
        r = self.c.post(url, data=data, environ_base=self.E)
        assert r.status_code in (200, 204, 302, 303), (url, r.status_code)
        return r

    def get(self, url):
        r = self.c.get(url, environ_base=self.E)
        if self.v15 and r.status_code == 302 and url.startswith("/session/") and url.count("/") == 2:
            r = self.c.get(r.headers["Location"], environ_base=self.E)
        assert r.status_code == 200, (url, r.status_code)
        return r

    def text(self, old, new):

        return new if self.v15 else old

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
                    outstanding={str(o["s"]["id"]): round(o["t"]["due"], 2)
                                 for o in seen["outstanding"] + seen.get("to_invoice", [])},
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
        assert (VW.config().get("gallery_host") or {}).get("type") == "local", "the web host was not saved"
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
        self._seen(folder, stamp(30)[:19] + "Z", ekey)
        self._send(folder, "picks", lambda: sealed(draft[:1], G.PICKS_AAD, stamp(26)), ekey, draft[:1], stamp(26))
        self._send(folder, "draft", lambda: sealed(draft, G.DRAFT_AAD, stamp(2)), ekey, draft, stamp(2))
        post("/gallery/sync")
        n, at = one("SELECT draft_n, draft_at FROM galleries WHERE session_id=?", (s_lane,))
        assert n == 3 and at, (n, at)
        assert one("SELECT retouch_picks FROM sessions WHERE id=?", (s_lane,))[0]
        self.s_lane = s_lane

    def _seen(self, folder, text, key=None):
        (folder / "seen.txt").write_text(text)

    def _send(self, folder, purpose, legacy, key, picks, at):

        (folder / f"{purpose}.bin").write_bytes(legacy())

    def _has(self, folder, plain, key=None):
        return (folder / plain).exists()

    def _path(self, folder, plain, key=None):
        return folder / plain

    def _names(self, folder, key=None):
        return {p.name for p in folder.iterdir()}

    def _picks_name(self, purpose):
        return f"{purpose}.bin"

    def _key(self, x, sid):

        key, ekey = x.one("SELECT key, ekey FROM galleries WHERE session_id=?", (sid,))
        return ekey or key

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
            if kind not in ("INV", "R"):
                continue
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

def page_text(html):

    import re
    return doc_text_any(re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", html))

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
        self._seen(folder, stamp(50)[:19] + "Z", ekey)
        self._send(folder, "picks", lambda: _sealed(G, ekey, picks, G.PICKS_AAD, stamp(48)), ekey, picks, stamp(48))
        post("/gallery/sync")
        assert one("SELECT retouch_picks FROM sessions WHERE id=?", (s_reyes,))[0], "the picks did not come in"
        assert x.text("Retouched finals", "Picks: 2 to retouch") in x.get(f"/c/{reyes}?s={s_reyes}").get_data(as_text=True)
        for i in (1, 2, 3):
            p_ = Path(x.work, f"final-{i}.jpg")
            _noisy_jpeg(p_, (160, 120), i)
            self._send_photo(x, s_reyes, p_.name, p_.read_bytes(), "final")
            p_.unlink()
        self._build(x, s_reyes, **{"from": "finals"})
        assert G.is_download(gid) and not G.online_download(gid)
        self._upload(x, s_reyes)
        assert G.online_download(gid) and not self._has(folder, "seen.txt", ekey)
        self._seen(folder, stamp(20)[:19] + "Z", ekey)
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
            blob = self._path(host, "manifest.bin", ekey or key).read_bytes()
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
    INQUIRY_EXT = ".bin"
    EARLIER = "host"
    BASE = "https://photos.example.com/g/"

    def gallery(self, x, s_lane):

        super().gallery(x, s_lane)
        self._move(x, s_lane)

    def _move(self, x, s_lane):
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
        names = self._names(new / gid, self._key(x, s_lane))
        assert {"index.html", "manifest.bin", "limits.txt", self._picks_name("picks"), self._picks_name("draft")} <= names, names
        assert self._has(Path(x.work, self.EARLIER, gid), "manifest.bin", self._key(x, s_lane))
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
        if hasattr(A, "sales_tax_pct"):
            assert x.in_request(lambda: A.sales_tax_pct()) == 6.0
        else:
            assert [t["rate"] for t in x.in_request(lambda: A.taxes_list())] == [6.0]

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
            self._seen(Path(x.work, self.HOST, gid), (now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ"), self._key(x, sid))
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
        assert self._has(Path(x.work, self.HOST, gone["Ivers"][1]), "manifest.bin", self._key(x, s_ivers))
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
            (web / "inquiries" / f"{i:016x}-{os.urandom(16).hex()}{self.INQUIRY_EXT}").write_bytes(S.seal_inquiry(pub, f_))
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

class More131(More130):

    def gallery(self, x, s_lane):

        More120.gallery(self, x, s_lane)
        _, s_moss = self._client(x, "Moss", "Rowan Moss", "rowan@example.com", "Moss family", f"{YEAR}-06-20", 150)
        x.post(f"/session/{s_moss}/stage", stage="booked")
        self._pay(x, s_moss, "balance", self._due(x, s_moss), f"{YEAR}-06-20")
        x.post(f"/session/{s_moss}/stage", stage="shot")
        from PIL import Image
        src = Path(x.work, "photos", "export", "moss")
        src.mkdir(parents=True)
        for i, col in enumerate(("olive", "navy"), 1):
            Image.new("RGB", (64, 48), col).save(src / f"moss{i}.jpg", "JPEG")
        self._build(x, s_moss, source_dir=str(src))
        self._upload(x, s_moss)
        gid = x.one("SELECT gallery_id FROM sessions WHERE id=?", (s_moss,))[0]
        k_ = self._key(x, s_moss)
        assert self._has(Path(x.work, self.EARLIER, gid), "manifest.bin", k_)
        self._move(x, s_lane)
        assert self._has(Path(x.work, self.HOST, gid), "manifest.bin", k_)
        assert self._has(Path(x.work, self.EARLIER, gid), "manifest.bin", k_)
        self.s_moss = s_moss

    def data(self, x):
        super().data(x)
        post, one, A = x.post, x.one, x.A
        from plugins import gallery as G
        from vault import web as VW
        from datetime import datetime, timedelta, timezone
        P = A.app.view_functions["build_gallery"].__globals__

        grant = [tuple(r) for r in x.rows("SELECT amount, label FROM charges WHERE session_id=?", (self.s130["grant"],))]
        assert grant == [(100.0, "Canceled, deposit kept")], grant

        s_moss = self.s_moss
        gid = one("SELECT gallery_id FROM sessions WHERE id=?", (s_moss,))[0]
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self._seen(Path(x.work, self.HOST, gid), (now - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ"), self._key(x, s_moss))
        post("/gallery/sync")
        post("/gallery/sync")
        g = one("SELECT upload_state, dark_at, moved_from FROM galleries WHERE session_id=?", (s_moss,))
        assert g[0] == "removed" and g[1] and g[2] is None, tuple(g)
        assert [p.name for p in Path(x.work, self.HOST, gid).iterdir()] == ["index.html"]
        old = Path(x.work, self.EARLIER, gid)
        assert not old.exists() or not any(old.iterdir()), sorted(p.name for p in old.iterdir())
        assert not one("SELECT 1 FROM gallery_leftovers WHERE gallery_id=?", (gid,))

        _, s_nash = self._client(x, "Nash", "Ellis Nash", "nash@example.com", "Nash family", f"{YEAR}-10-24", 150)
        post(f"/session/{s_nash}/stage", stage="booked")
        self._pay(x, s_nash, "balance", self._due(x, s_nash), f"{YEAR}-10-24")
        post(f"/session/{s_nash}/stage", stage="shot")
        from PIL import Image
        src = Path(x.work, "photos", "export", "nash")
        src.mkdir(parents=True)
        for i, col in enumerate(("sienna", "steelblue"), 1):
            Image.new("RGB", (64, 48), col).save(src / f"nash{i}.jpg", "JPEG")
        self._build(x, s_nash, source_dir=str(src))
        real = P["upload_gallery"]

        def partway(*a, **k):
            real(*a, **k)
            raise IOError("the host dropped the connection")
        P["upload_gallery"] = partway
        try:
            post(f"/session/{s_nash}/gallery/upload")
            _wait_threads(f"gal-up-{s_nash}")
        finally:
            P["upload_gallery"] = real
        g = one("SELECT upload_state, uploaded_at, host_fp FROM galleries WHERE session_id=?", (s_nash,))
        assert g[0] == "error" and g[1] is None and g[2] == G.fingerprint_of(VW.config()["gallery_host"]), tuple(g)
        gid_n = one("SELECT gallery_id FROM sessions WHERE id=?", (s_nash,))[0]
        assert self._has(Path(x.work, self.HOST, gid_n), "manifest.bin", self._key(x, s_nash))

        root = Path(x.work, self.EARLIER, ".vault-root")
        root.rename(root.with_name(".vault-root-off"))
        try:
            post("/gallery/sync")
        finally:
            root.with_name(".vault-root-off").rename(root)
        fp_old = G.fingerprint_of(G.earlier_host_cfgs(VW.config())[0])
        rows_ = [tuple(r) for r in x.rows("SELECT key FROM settings WHERE key LIKE 'gallery_unreachable:%'")]
        assert rows_ == [(P["UNREACHABLE_KEY"] + fp_old,)], rows_
        assert P["_UNREACHABLE"] == {}
        assert not x.in_request(lambda: P["may_try"](fp_old))

        page = x.get("/security").get_data(as_text=True)
        import datakey
        self.pw_work = datakey.password_work()
        assert self.pw_work and f"scrypt work factor {self.pw_work}" in page, self.pw_work
        self.s131 = dict(moss=s_moss, nash=s_nash)

    def measure(self, x, expected):
        out = super().measure(x, expected)
        rows, one = x.rows, x.one
        return dict(
            out,
            sessions_by_name=dict(out["sessions_by_name"], **self.s131),
            unreachable={r[0]: r[1] for r in rows("SELECT key, value FROM settings WHERE key LIKE 'gallery_unreachable:%'")},
            upload_errors={str(r[0]): r[1] for r in rows(
                "SELECT session_id, upload_error FROM galleries WHERE upload_state='error' ORDER BY session_id")},
            password_work=self.pw_work,
        )

def scenario_131(x, made_with):
    return scenario_101(x, made_with, more=More131())

class More140(More131):

    def _seen(self, folder, text, key=None):
        from helpers import host_write
        host_write(folder, "seen.txt", text, key)

    def _send(self, folder, purpose, legacy, key, picks, at):
        from helpers import host_write, seal_picks
        host_write(folder, f"{purpose}.las", seal_picks(key, folder.name, picks, purpose, at), key)

    def _has(self, folder, plain, key=None):
        from helpers import host_has
        return host_has(folder, plain, key)

    def _path(self, folder, plain, key=None):
        from helpers import hosted
        return hosted(folder, plain, key)

    def _names(self, folder, key=None):
        from helpers import host_names
        return host_names(folder, key)

    def _picks_name(self, purpose):
        return f"{purpose}.las"

    INQUIRY_EXT = ".las"

    PUBLIC = "photos.example.com"

    def _serve(self, x):

        import atexit
        import socket
        import time
        import urllib.error
        import urllib.request
        import hostcheck
        import webtls
        from test_web_cert_pin import Front, make_cert
        certs = Path(tempfile.mkdtemp(prefix="lumenaureum-140-cert-"))
        self.cert = make_cert(certs, "web", names=(self.PUBLIC,))
        s_ = socket.socket()
        s_.bind(("127.0.0.1", 0))
        port = s_.getsockname()[1]
        s_.close()
        self.php = subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", "-t", str(Path(x.work, self.HOST).parent)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(self._stop)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        self.front = Front(self.cert[2], self.cert[3], backend=port)
        real_connect = socket.create_connection

        def resolve(addr, *a, **kw):
            if addr[0] == self.PUBLIC and int(addr[1]) == 443:
                addr = ("127.0.0.1", self.front.port)
            return real_connect(addr, *a, **kw)
        socket.create_connection = resolve
        real_http = hostcheck.HTTP

        def http(method, url, headers=None, data=None, timeout=20):

            from urllib.parse import urlsplit
            if (urlsplit(url).hostname or "") != self.PUBLIC:
                return real_http(method, url, headers=headers, data=data, timeout=timeout)
            req = urllib.request.Request(url, data=data, method=method,
                                         headers=dict(headers or {}, **{"User-Agent": "Lumen Aureum"}))
            deadline = time.monotonic() + 2 * timeout
            try:
                with webtls.open_request(req, timeout=timeout) as r:
                    return r.status, {k.lower(): v for k, v in r.headers.items()}, webtls.read(r, 2_000_000, deadline)
            except urllib.error.HTTPError as e:
                try:
                    body = webtls.read(e, 2_000_000, deadline)
                except Exception:
                    body = b""
                return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body
        hostcheck.HTTP = http

    def _stop(self):
        if getattr(self, "front", None):
            self.front.close()
            self.front = None
        if getattr(self, "php", None):
            self.php.terminate()
            self.php.wait(timeout=10)
            self.php = None

    def _photo(self, path, size, color):

        from PIL import Image
        im = Image.new("RGB", size, color)
        for x_ in range(0, size[0], 37):
            im.putpixel((x_, x_ % size[1]), (255, 255, 255))
        im.save(path, "JPEG")

    def _offer(self, x, sid):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from helpers import hosted
        G = x.A.app.config["gallery_module"]
        gid, key = x.one("SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0], self._key(x, sid)
        blob = hosted(Path(x.work, self.HOST, gid), "offer.bin", key).read_bytes()
        return json.loads(AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], b"lumen-aureum-gallery:offer.bin"))

    def _order(self, x, sid, oid, who, ship, lines):

        import base64
        from datetime import datetime, timezone
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import printorders as P
        G = x.A.app.config["gallery_module"]
        gid, key = x.one("SELECT gallery_id FROM sessions WHERE id=?", (sid,))[0], self._key(x, sid)
        off = self._offer(x, sid)
        blob = (G.OUT_ROOT / gid / "manifest.bin").read_bytes()
        m = json.loads(AESGCM(G.key_bytes(key)).decrypt(blob[:12], blob[12:], b"lumen-aureum-gallery:manifest.bin"))
        by_n = {it["n"]: it for it in m["items"]}
        item = {i["label"].split(" ")[0]: i for i in off["items"]}
        out = []
        for name, size, qty in lines:
            it = by_n[name]
            w, h = (float(v) for v in size.split("x"))
            crop = P.full_crop(it["ow"], it["oh"], w, h)
            out.append(dict(p=it["o"], n=name, item=item[size]["id"], qty=qty, crop=crop, turn=0, each=item[size]["each"],
                            ppi=P.effective_ppi(crop, it["ow"], it["oh"], w, h, 0)))
        at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        data = dict(v=1, oid=oid, at=at, plv=off["plv"], cur="USD", who=who, ship=ship, lines=out,
                    sub=sum(l_["each"] * l_["qty"] for l_ in out), ack=True, rel=dict(kind="new", prev=None), note="")
        spk = base64.urlsafe_b64decode(off["spk"] + "=" * (-len(off["spk"]) % 4))
        env = P.seal_order(data, spk, gid, G.key_bytes(key))
        d = Path(x.work, self.HOST, gid, "orders")
        d.mkdir(exist_ok=True)
        (d / P.host_name(env)).write_bytes(env)

    def data(self, x):
        super().data(x)
        post, one, A = x.post, x.one, x.A
        import webtls
        from vault import web as VW

        self._serve(x)
        addr = webtls.address(self.BASE)
        post("/setup/web-cert/check", addr=addr)
        seen = json.loads(one("SELECT value FROM settings WHERE key=?", (webtls.SEEN_KEY + addr,))[0])
        assert seen["kind"] == "untrusted" and seen["cert"]["self_signed"], seen
        page = x.get("/setup/galleries").get_data(as_text=True)
        assert seen["cert"]["fp"] in page, "Setup does not show the certificate"
        post("/setup/web-cert/trust", cert=f"{addr} {seen['cert']['sha256']}")
        pin = (VW.config().get(webtls.STORE) or {}).get(addr)
        assert pin and pin["sha256"] == seen["cert"]["sha256"] and len(pin["spki"]) == 64, pin
        caps = json.loads(one("SELECT value FROM settings WHERE key='gallery_host_caps'")[0])
        assert caps["now"]["kind"] == "ok", caps
        post("/gallery/sync")
        _wait_threads("gal-")

        h_wren, s_wren = self._client(x, "Wren", "Harper Wren", "harper@example.com", "Wren family", f"{YEAR}-10-17", 350)
        post(f"/session/{s_wren}/delivery", delivery_method="link")
        post(f"/session/{s_wren}/stage", stage="booked")
        self._pay(x, s_wren, "balance", self._due(x, s_wren), f"{YEAR}-10-17")
        post(f"/session/{s_wren}/stage", stage="shot")
        src = Path(x.work, "photos", "export", "wren")
        src.mkdir(parents=True)
        self._photo(src / "Wren-01.jpg", (3000, 2000), "darkolivegreen")
        self._photo(src / "Wren-02.jpg", (2000, 3000), "slateblue")
        self._build(x, s_wren, source_dir=str(src))
        self._upload(x, s_wren)
        post(f"/session/{s_wren}/gallery/no-picks")
        self._build(x, s_wren, source_dir=str(src))
        self._upload(x, s_wren)
        G = A.app.config["gallery_module"]
        assert G.online_download(one("SELECT gallery_id FROM sessions WHERE id=?", (s_wren,))[0])

        assert one("SELECT value FROM settings WHERE key='plugin_on_prints'") is None
        post("/settings/plugins", name="prints", turn="on")
        assert one("SELECT value FROM settings WHERE key='plugin_on_prints'")[0] == "1"
        x.get("/settings/prints")
        cols = ("id", "label", "w", "h", "finish", "each_cents", "cost_cents", "ppi_hide", "ppi_mark", "offered")
        rows_ = [dict(zip(cols, r)) for r in x.rows(f"SELECT {', '.join(cols)} FROM price_list_items ORDER BY sort")]
        assert rows_, "no SAMPLE price list"
        form = {"id": [r["id"] for r in rows_], "markup": "3", "invoice_days": "3", "large_n": "25", "large_c": "250",
                "arrive": "Prints come straight from our lab."}
        for r in rows_:
            i = r["id"]
            form.update({f"label_{i}": r["label"], f"w_{i}": f"{r['w']:g}", f"h_{i}": f"{r['h']:g}", f"finish_{i}": r["finish"],
                         f"each_{i}": f"{r['each_cents'] / 100:.2f}", f"hide_{i}": str(r["ppi_hide"]),
                         f"mark_{i}": str(r["ppi_mark"]), f"on_{i}": "1"})
            if r["label"].startswith(("4x6", "8x10")):
                form[f"cost_{i}"] = "0.95" if r["label"].startswith("4x6") else "3.40"
            if r["label"].startswith("16x20"):
                form[f"each_{i}"] = "165.00"
        for d_ in ("us", "akhi", "pickup"):
            form.update({f"ship_on_{d_}": "1", f"ship_rule_{d_}": "cost"})
        form["ship_rule_ca"] = "cost"
        r_ = x.c.post("/settings/prints", data=form, environ_base=x.E)
        assert r_.status_code == 302 and one("SELECT value FROM settings WHERE key='prints_saved'")[0] == "1"
        post("/settings/prints/fill")
        post("/settings/prints/switch", offer="1")
        post("/gallery/sync")
        off = self._offer(x, s_wren)
        assert {"4x6", "5x7", "8x10"} <= {i["label"].split(" ")[0] for i in off["items"]}, off

        me = dict(how="me", name="", addr1="12 Elm St", addr2="", city="Springfield", state="IL", zip="62704",
                  country="US", phone="")
        harper = dict(name="Harper Wren", email="harper@example.com", phone="")
        oids = [f"{n:x}" * 32 for n in range(10, 16)]
        for oid in oids[:5]:
            self._order(x, s_wren, oid, harper, me, [("Wren-01.jpg", "8x10", 2), ("Wren-02.jpg", "4x6", 1)])
        self._order(x, s_wren, oids[5], dict(name="Dale Rivers", email="dale@example.org", phone=""),
                    dict(how="other", name="Dale Rivers", addr1="9 Oak Ln", addr2="", city="Peoria", state="IL",
                         zip="61602", country="US", phone=""), [("Wren-02.jpg", "5x7", 1)])
        post("/gallery/sync")
        got = {r[0]: r[1] for r in x.rows("SELECT oid, id FROM print_orders")}
        assert set(got) == set(oids), got
        o_new, o_part, o_paid, o_ship, o_cancel, o_guest = (got[o] for o in oids)
        assert one("SELECT state FROM print_orders WHERE id=?", (o_guest,))[0] == "unmatched"

        def invoice(oid_):
            post(f"/prints/order/{oid_}/shipping", lab_ship="8.00")
            post(f"/prints/order/{oid_}/invoice")
            return json.loads(one("SELECT invoice_data FROM print_orders WHERE id=?", (oid_,))[0])["total"]

        def pay(oid_, cents, day, kind="payment"):
            post(f"/prints/order/{oid_}/payment", kind=kind, amount=f"{cents / 100:.2f}", received=day, method="card")

        def looks_right(oid_):
            for (lid,) in x.rows("SELECT id FROM print_order_lines WHERE order_id=? AND crop_confirmed=0", (oid_,)):
                post(f"/prints/order/{oid_}/line/{lid}", do="confirm")

        invoice(o_part)
        pay(o_part, 1000, f"{YEAR}-11-03")
        total = invoice(o_paid)
        pay(o_paid, total + 500, f"{YEAR}-11-04")
        looks_right(o_paid)
        total = invoice(o_ship)
        pay(o_ship, total, f"{YEAR}-11-05")
        looks_right(o_ship)
        post(f"/prints/order/{o_ship}/ordered", lab_name="Sample Lab", lab_number="L-1001", cost_products="12.40",
             cost_shipping="6.75", cost_tax="0.80")
        r_ = x.c.post(f"/prints/order/{o_ship}/shipped", environ_base=x.E, data=dict(
            carrier="UPS", tracking="1Z999AA10123456784", url="https://track.example.com/1Z999AA10123456784"))
        assert r_.status_code == 302
        total = invoice(o_cancel)
        pay(o_cancel, total, f"{YEAR}-11-06")
        post(f"/prints/order/{o_cancel}/cancel")
        pay(o_cancel, total, f"{YEAR}-11-07", kind="refund")
        states = {r[0]: r[1] for r in x.rows("SELECT id, state FROM print_orders")}
        assert states == {o_new: "new", o_part: "invoiced", o_paid: "paid", o_ship: "shipped", o_cancel: "canceled",
                          o_guest: "unmatched"}, states
        assert one("SELECT COUNT(*) FROM print_lab_costs WHERE order_id=?", (o_ship,))[0] == 1

        _, s_olsen = self._client(x, "Olsen", "Taylor Olsen", "taylor@example.com", "Olsen family", f"{YEAR}-10-31", 200)
        self._pay(x, s_olsen, "deposit", 100, f"{YEAR}-10-02")
        post(f"/session/{s_olsen}/stage", stage="booked")
        post(f"/session/{s_olsen}/stage", stage="shot")
        self._pay(x, s_olsen, "balance", self._due(x, s_olsen), f"{YEAR}-10-31")
        post(f"/session/{s_olsen}/money", what="payment", kind="refund", amount="30.00", method="card",
             received=f"{YEAR}-11-02", after_refund="hold")
        assert one("SELECT refund_release FROM sessions WHERE id=?", (s_olsen,))[0] == "hold"

        import io
        lane = one("SELECT household_id FROM sessions WHERE id=?", (self.s_lane,))[0]
        r_ = x.c.post(f"/client/{lane}/face", data={"photo": (io.BytesIO(_jpeg("#b08060", (480, 640))), "face.jpg")},
                      content_type="multipart/form-data", environ_base=x.E)
        assert r_.status_code in (302, 303)
        assert Path(x.work, "faces", f"{lane}.jpg").is_file()
        self.s140 = dict(wren=s_wren, olsen=s_olsen)
        self.h140 = dict(wren=h_wren, lane=lane)
        self.orders = dict(new=o_new, invoiced=o_part, paid=o_paid, shipped=o_ship, canceled=o_cancel, unmatched=o_guest)

    def measure(self, x, expected):
        try:
            out = super().measure(x, expected)
            return dict(out, **self._measure140(x, out))
        finally:
            self._stop()

    def _measure140(self, x, out):
        import hashlib
        import webtls
        from vault import web as VW
        A, one, rows, in_request = x.A, x.one, x.rows, x.in_request
        G = A.app.config["gallery_module"]

        def table(name, order="id"):

            con = A.connect()
            try:
                cur = con.execute(f"SELECT * FROM {name} ORDER BY {order}")
                cols = [d[0] for d in cur.description]
                got = cur.fetchall()
            finally:
                con.close()
            fix = lambda v: (hashlib.sha256(v if isinstance(v, bytes) else v.encode()).hexdigest()
                             if isinstance(v, bytes) or (isinstance(v, str) and len(v) > 400) else v)
            return [{c: fix(v) for c, v in zip(cols, r)} for r in got]
        prints_tables = {t: table(t, o) for t, o in (
            ("print_orders", "id"), ("print_order_lines", "id"), ("print_payments", "id"), ("print_lab_costs", "id"),
            ("print_galleries", "session_id"), ("print_order_oids", "oid"), ("price_list_items", "sort, id"),
            ("price_list_versions", "plv"))}
        print_invoices = {}
        for oid_, number in rows("SELECT id, invoice_number FROM print_orders WHERE invoice_number IS NOT NULL ORDER BY id"):
            print_invoices[str(oid_)] = dict(number=number, text=page_text(
                x.get(f"/prints/order/{oid_}/invoice").get_data(as_text=True)))
        year = in_request(lambda: A.app.config["prints_year"](YEAR))
        heads = {}
        for sid in (self.s130["grant"], self.s140["olsen"], self.s140["wren"]):
            if x.v15:
                page = x.get(f"/session/{sid}/edit").get_data(as_text=True)
                heads[str(sid)] = doc_text_any(page.split('<table class="ui-money" aria-label="Summary">', 1)[1]
                                               .split("</table>", 1)[0])
                continue
            page = x.get(f"/session/{sid}").get_data(as_text=True)
            heads[str(sid)] = doc_text_any(page.split('<div class="surface highlights">', 1)[1].split("<section", 1)[0])
        assert "Deposit kept $100" in heads[str(self.s130["grant"])], heads
        face = x.get(f"/face/{self.h140['lane']}").data
        addr = webtls.address(self.BASE)
        caps = json.loads(one("SELECT value FROM settings WHERE key='gallery_host_caps'")[0])
        links = {str(r[0]): dict(gallery_id=r[1], link_was=r[2], sealed_gen=r[3], page_set=r[4])
                 for r in rows("""SELECT s.id, s.gallery_id, g.link_was, g.sealed_gen, g.page_set FROM galleries g
                                  JOIN sessions s ON s.id=g.session_id ORDER BY s.id""")}
        assert all(v["gallery_id"].startswith("g-") for v in links.values()), links
        kept = G.KEPT_ROOT / links[str(self.s_lane)]["gallery_id"]
        prints_settings = {r[0]: r[1] for r in rows(
            "SELECT key, value FROM settings WHERE key LIKE 'prints\\_%' ESCAPE '\\' OR key LIKE 'plugin\\_%' ESCAPE '\\' "
            "OR key IN ('tax_prints', 'tax_print_shipping') ORDER BY key")}
        return dict(
            sessions_by_name=dict(out["sessions_by_name"], **self.s140),
            print_orders=self.orders, prints_tables=prints_tables, print_invoices=print_invoices,
            prints_year=year, prints_settings=prints_settings,
            headers=heads,
            refund_release={str(r[0]): r[1] for r in rows(
                "SELECT id, refund_release FROM sessions WHERE refund_release IS NOT NULL ORDER BY id")},
            release_blocked={str(r[0]): round(in_request(lambda: A.release_blocked(r[0])), 2)
                             for r in rows("SELECT id FROM sessions ORDER BY id")},
            face=dict(household=self.h140["lane"], sha256=hashlib.sha256(face).hexdigest(), size=len(face)),
            web_cert=dict(address=addr, pins=VW.config().get(webtls.STORE) or {},
                          seen={r[0]: r[1] for r in rows(
                              "SELECT key, value FROM settings WHERE key LIKE 'web\\_cert\\_%' ESCAPE '\\'")}),
            host_caps=dict(fp=caps.get("fp"), setup_kind=caps["setup"]["kind"], now_kind=caps["now"]["kind"],
                           features=caps["now"].get("features")),
            links=links,
            kept_picks=sorted(p.name for p in kept.iterdir()) if kept.is_dir() else [],
            upgrade_140=json.loads((one("SELECT value FROM settings WHERE key='upgrade_140'") or ["{}"])[0]),

            studio_files=[[n_, fp_, sha_] for n_, fp_, _size, sha_ in out["studio_files"]],
        )

def scenario_140(x, made_with):
    return scenario_101(x, made_with, more=More140())

LICENSE_LINE = "Your license to use the photos is set out in your agreement"

def form_fields(html, action=None):

    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.forms, self.cur, self.sel, self.opt, self.area = [], None, None, None, None

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "form":
                self.cur = dict(action=a.get("action"), fields=[])
                self.forms.append(self.cur)
            elif self.cur is None:
                return
            elif tag == "input" and a.get("name"):
                kind = (a.get("type") or "text").lower()
                if kind in ("checkbox", "radio"):
                    if "checked" in a:
                        self.cur["fields"].append((a["name"], a.get("value", "on")))
                elif kind not in ("submit", "button", "file", "image", "reset"):
                    self.cur["fields"].append((a["name"], a.get("value", "")))
            elif tag == "select" and a.get("name"):
                self.sel = dict(name=a["name"], first=None, chosen=None)
            elif tag == "option" and self.sel is not None:
                v = a.get("value")
                self.opt = v
                if self.sel["first"] is None:
                    self.sel["first"] = v
                if "selected" in a:
                    self.sel["chosen"] = v
            elif tag == "textarea" and a.get("name"):
                self.area = [a["name"], ""]

        def handle_data(self, data):
            if self.area is not None:
                self.area[1] += data

        def handle_endtag(self, tag):
            if tag == "form":
                self.cur = None
            elif tag == "select" and self.sel is not None:
                v = self.sel["chosen"] if self.sel["chosen"] is not None else self.sel["first"]
                if v is not None and self.cur is not None:
                    self.cur["fields"].append((self.sel["name"], v))
                self.sel = None
            elif tag == "textarea" and self.area is not None:
                if self.cur is not None:
                    self.cur["fields"].append((self.area[0], self.area[1].lstrip("\n")))
                self.area = None
    p = P()
    p.feed(html)
    form = next(f_ for f_ in p.forms if f_["action"] == action)
    out = {}
    for k, v in form["fields"]:
        out.setdefault(k, []).append(v)
    return out

class More141(More140):

    S3_BUCKET = "studio-backups"

    def data(self, x):
        super().data(x)
        self._s3(x)
        self._codeword_types(x)
        self._replace_link(x)
        self._served(x)
        self._receipts(x)

    def _s3(self, x):

        from fake_s3 import ACCESS, SECRET, FakeS3, make_cert
        import setup as SU
        from vault import s3 as S3
        from vault import web as VW
        certs = Path(tempfile.mkdtemp(prefix="lumenaureum-141-s3-"))
        self.s3 = FakeS3(buckets=(self.S3_BUCKET,), tls=make_cert(certs))
        addr = f"127.0.0.1:{self.s3.port}"
        prim = VW.config().get("primary") or {}
        assert prim.get("type") == "local", prim
        real_same = SU.same_disk
        SU.same_disk = lambda a, b: False
        try:
            form = dict(primary_type="local", primary_path=prim["path"], primary_init="1", secondary_type="s3",
                        secondary_bucket=self.S3_BUCKET, secondary_endpoint=self.s3.endpoint,
                        secondary_access_key=ACCESS, secondary_secret_key=SECRET, secondary_addressing="path",
                        backup_time="02:40")
            r = x.c.post("/setup/storage", data=form, environ_base=x.E)
            page = r.get_data(as_text=True).replace("&#39;", "'")
            assert r.status_code == 200 and "Its certificate isn't from a public authority" in page, page.split('<div class="err">', 1)[-1][:600]
            assert "secondary" not in VW.config() and not self.s3.log
            seen = json.loads(x.one("SELECT value FROM settings WHERE key=?", (S3.SEEN_KEY + addr,))[0])
            assert seen["kind"] == "untrusted" and seen["cert"]["fp"] in page, seen
            r = x.c.post("/setup/storage", data=dict(form, s3cert_trust=f"{addr} {seen['cert']['sha256']}"), environ_base=x.E)
            assert r.status_code == 302, r.get_data(as_text=True)[-2000:]
        finally:
            SU.same_disk = real_same
        cfg = VW.config()
        assert cfg["secondary"]["type"] == "s3" and cfg["secondary"]["endpoint"] == self.s3.endpoint, cfg["secondary"]
        assert cfg["primary"] == prim
        assert cfg[S3.STORE][addr]["sha256"] == seen["cert"]["sha256"]
        assert any(m == "PUT" for m, *_ in self.s3.log) and not self.s3.objects[self.S3_BUCKET]
        assert not x.one("SELECT 1 FROM settings WHERE key=?", (S3.SEEN_KEY + addr,))
        assert "The one you confirmed on" in x.get("/setup/storage").get_data(as_text=True).replace("&#39;", "'")
        self.s3_addr = addr

    def _codeword_types(self, x):
        A = x.A
        assert all(A.C.POLICY[t].get("gallery_codeword") is True for t in A.C.SESSION_TYPES), \
            {t: A.C.POLICY[t].get("gallery_codeword") for t in A.C.SESSION_TYPES}
        fields = form_fields(x.get("/settings/type/headshot").get_data(as_text=True))
        assert fields.pop("gallery_codeword") == ["1"], "the codeword box was not ticked"
        r = x.c.post("/settings/type/headshot", data=fields, environ_base=x.E)
        assert r.status_code == 302, r.status_code
        assert A.C.POLICY["headshot"].get("gallery_codeword") is False
        assert A.C.POLICY["family"].get("gallery_codeword") is True
        others = {k: v for k, v in A.C.POLICY["headshot"].items() if k != "gallery_codeword"}
        self.headshot_policy = others

        _, s_upton, asked = self._inquiry(x, "Upton", "Casey Upton", "upton@example.com", "family", "Upton family",
                                          f"{YEAR}-12-12", gallery_codeword=self.CODEWORD_141)
        assert asked, "a family inquiry does not ask for a codeword"
        assert x.one("SELECT gallery_codeword FROM sessions WHERE id=?", (s_upton,))[0] == self.CODEWORD_141
        self.s_upton = s_upton

    def _inquiry(self, x, name, contact, email, type_, title, shoot, **extra):

        x.post("/contact/new", contact_name=contact, household_name=name, email=email)
        hid = x.one("SELECT id FROM households WHERE name=?", (name,))[0]
        page = x.get(f"/inquiry?hid={hid}&type={type_}").get_data(as_text=True)
        x.post("/inquiry", type=type_, household_id=str(hid), title=title, shoot_date=shoot, **extra)
        sid = x.one("SELECT MAX(id) FROM sessions WHERE household_id=?", (hid,))[0]
        return hid, sid, 'name="gallery_codeword"' in page

    def _replace_link(self, x):

        post, one = x.post, x.one
        from datetime import datetime, timedelta, timezone
        from PIL import Image
        h_yates, s_yates, asked = self._inquiry(x, "Yates", "Morgan Yates", "yates@example.com", "family", "Yates family",
                                                f"{YEAR}-11-08", gallery_codeword="")
        assert asked, "a family inquiry does not ask for a codeword"
        assert one("SELECT gallery_codeword FROM sessions WHERE id=?", (s_yates,))[0] in ("", None)
        post(f"/session/{s_yates}/money", what="line", label="Family session", amount="200")
        post(f"/session/{s_yates}/stage", stage="booked")
        self._pay(x, s_yates, "balance", self._due(x, s_yates), f"{YEAR}-11-08")
        post(f"/session/{s_yates}/stage", stage="shot")
        src = Path(x.work, "photos", "export", "yates")
        src.mkdir(parents=True)
        for i, col in enumerate(("peru", "cadetblue", "rosybrown"), 1):
            Image.new("RGB", (64, 48), col).save(src / f"yates{i}.jpg", "JPEG")
        self._build(x, s_yates, source_dir=str(src))
        self._upload(x, s_yates)
        old_gid = one("SELECT gallery_id FROM sessions WHERE id=?", (s_yates,))[0]
        old_key = self._key(x, s_yates)
        folder = Path(x.work, self.HOST, old_gid)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = lambda hours: (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        draft = [dict(i=0, name="yates1.jpg", r=True, note="a little brighter", m=False, v=False),
                 dict(i=2, name="yates3.jpg", r=False, note="", m=True, v=False)]
        self._seen(folder, stamp(10)[:19] + "Z", old_key)
        self._send(folder, "picks", None, old_key, draft[:1], stamp(8))
        self._send(folder, "draft", None, old_key, draft, stamp(3))
        post("/gallery/sync")
        assert one("SELECT draft_n FROM galleries WHERE session_id=?", (s_yates,))[0] == 2
        G = x.A.app.config["gallery_module"]
        assert sorted(p.name for p in (G.KEPT_ROOT / old_gid).iterdir()) == ["draft.las", "picks.las"]
        hid = one("SELECT household_id FROM sessions WHERE id=?", (s_yates,))[0]
        assert "Replace this link" in x.get(f"/c/{hid}?s={s_yates}").get_data(as_text=True)
        post(f"/session/{s_yates}/gallery/replace-link")
        _wait_threads(f"gallery-{s_yates}")
        new_gid, state, built = one("""SELECT s.gallery_id, g.upload_state, g.built_at FROM sessions s
                                       JOIN galleries g ON g.session_id=s.id WHERE s.id=?""", (s_yates,))
        assert new_gid != old_gid and new_gid.startswith("g-") and state is None and built
        assert not folder.exists(), "the old link still opens something"
        self._upload(x, s_yates)
        new_key = self._key(x, s_yates)
        assert new_key != old_key
        names = self._names(Path(x.work, self.HOST, new_gid), new_key)
        assert {"index.html", "picks.las", "draft.las"} <= names, names
        assert one("SELECT 1 FROM tasks WHERE session_id=? AND title='Send the client the new gallery link' AND done IS NULL",
                   (s_yates,))
        self.s141 = dict(yates=s_yates, upton=self.s_upton)
        self.h141 = dict(yates=h_yates)
        self.replaced = dict(session=s_yates, old_gallery_id=old_gid)

    CODEWORD_141 = "copper lantern meadow"

    def _served(self, x):

        A, one = x.A, x.one
        check = A.app.config["gallery_served_check"]

        def run():
            with A.app.app_context():
                check(force=True)
                A.db().commit()
        x.post("/gallery/sync")
        run()
        st = json.loads(one("SELECT value FROM settings WHERE key='served_check'")[0])
        assert st["bad"] == {} and st["last"], st
        s_ivers = self.s130["ivers"]
        gid = one("SELECT gallery_id FROM sessions WHERE id=?", (s_ivers,))[0]
        page = Path(x.work, self.HOST, gid, "index.html")
        page.write_bytes(page.read_bytes().replace(b"</body>", b"<!-- changed on the web host --></body>"))
        run()
        run()
        st = json.loads(one("SELECT value FROM settings WHERE key='served_check'")[0])
        assert list(st["bad"]) == [gid] and st["bad"][gid]["n"] == 2 and st["bad"][gid]["sid"] == s_ivers, st
        assert "is not what Lumen Aureum put there" in x.get("/").get_data(as_text=True)
        assert one("SELECT 1 FROM audit WHERE session_id=? AND action LIKE 'The gallery page or scripts on the web host%'",
                   (s_ivers,))

    def _receipts(self, x):
        post, one = x.post, x.one
        s_price = self.s_price
        assert x.in_request(lambda: x.A.licensed(s_price))
        assert one("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement' AND status='signed'", (s_price,))
        self._pay(x, s_price, "balance", self._due(x, s_price), f"{YEAR}-11-09")
        post(f"/session/{s_price}/receipt/issue")
        assert LICENSE_LINE in x.get(f"/session/{s_price}/receipt").get_data(as_text=True)
        _, s_vance, asked = self._inquiry(x, "Vance", "Jules Vance", "vance@example.com", "headshot", "Vance headshots",
                                          f"{YEAR}-11-10")
        assert not asked, "a headshot inquiry still asks for a codeword"
        assert x.in_request(lambda: x.A.licensed(s_vance))
        assert not one("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement' AND status='signed'", (s_vance,))
        post(f"/session/{s_vance}/money", what="line", label="Headshot session", amount="150")
        self._pay(x, s_vance, "balance", self._due(x, s_vance), f"{YEAR}-11-10")
        post(f"/session/{s_vance}/receipt/issue")
        page = x.get(f"/session/{s_vance}/receipt").get_data(as_text=True)
        assert "Thank you." in page and LICENSE_LINE not in page and "copyright in your delivered photos" not in page
        self.s141["vance"] = s_vance
        self.receipts = {str(s_price): True, str(s_vance): False}

    def _measure140(self, x, out):
        got = super()._measure140(x, out)
        try:
            return dict(got, **self._measure141(x, dict(out, **got)))
        finally:
            if getattr(self, "s3", None):
                self.s3.close()
                self.s3 = None

    def _measure141(self, x, out):
        import hashlib
        from vault import s3 as S3
        from vault import web as VW
        A, one, rows = x.A, x.one, x.rows
        cfg = VW.config()
        G = A.app.config["gallery_module"]
        s_yates = self.s141["yates"]
        gid = one("SELECT gallery_id FROM sessions WHERE id=?", (s_yates,))[0]
        receipts = {}
        for sid, has in self.receipts.items():
            number = one("SELECT number FROM doc_numbers WHERE session_id=? AND kind='R'", (int(sid),))[0]
            receipts[sid] = dict(number=number, license_line=has)
        return dict(
            sessions_by_name=dict(out["sessions_by_name"], **self.s141),
            s3=dict(address=self.s3_addr, bucket=self.S3_BUCKET,
                    secondary={k: v for k, v in cfg["secondary"].items() if k != "secret_key"},
                    pins=cfg.get(S3.STORE) or {}),
            primary_type=cfg["primary"]["type"],
            storage_keys=sorted(cfg),
            codeword_policy={t: A.C.POLICY[t].get("gallery_codeword") for t in sorted(A.C.SESSION_TYPES)},
            headshot_policy=self.headshot_policy,
            rules_sha256=hashlib.sha256(one("SELECT value FROM settings WHERE key='rules'")[0].encode()).hexdigest(),
            codewords={str(r[0]): r[1] for r in rows(
                "SELECT id, gallery_codeword FROM sessions WHERE gallery_codeword != '' ORDER BY id")},
            replaced=dict(self.replaced, gallery_id=gid,
                          kept=sorted(p.name for p in (G.KEPT_ROOT / gid).iterdir()) if (G.KEPT_ROOT / gid).is_dir() else [],
                          history=[r[0] for r in rows(
                              "SELECT action FROM audit WHERE session_id=? AND action LIKE 'Replaced the gallery%' ORDER BY id",
                              (s_yates,))],
                          tasks=[list(r) for r in rows(
                              "SELECT title, done FROM tasks WHERE session_id=? AND title='Send the client the new gallery link'",
                              (s_yates,))]),
            served_check=json.loads(one("SELECT value FROM settings WHERE key='served_check'")[0]),
            served_history={str(r[0]): r[1] for r in rows(
                "SELECT session_id, action FROM audit WHERE action LIKE 'The gallery page or scripts on the web host%'")},
            receipts=receipts,
        )

def scenario_141(x, made_with):
    return scenario_101(x, made_with, more=More141())

class More150(More141):

    IDLE_HOURS, CHECK_IN_MONTHS, MILEAGE_RATE, HOME_BASE = "4", "9", "0.70", "12 River Road, Lark Falls"
    NTFY = dict(server="https://ntfy.example.com", topic="lark-pine-studio-7f3k2q", token="tk_fixture150example")
    RELEASE_ADDED = "Photos of guests are never posted without their consent."
    LABELS = (("ellis", "Slow to pay"), ("ellis", "Prefers text"), ("zeller", "Referral"))

    def data(self, x):
        super().data(x)
        self.s150, self.h150 = {}, {}
        self._settings150(x)
        self._zeller(x)
        self._not_booked(x)
        self._clients150(x)

    def _settings150(self, x):
        post, one = x.post, x.one
        fields = form_fields(x.get("/settings").get_data(as_text=True))
        rebuilt = "main_posted" not in fields
        on_general = "mileage_rate" in fields
        if rebuilt:
            r = x.c.post("/settings?tab=clients", data={"clients_posted": "1", "form_id": "clients-form",
                                                        "check_in_snooze_months": self.CHECK_IN_MONTHS, "age_majority": "18"},
                         environ_base=x.E)
            assert r.status_code == 302, r.status_code
            r = x.c.post("/settings", data={"idle_posted": "1", "idle_signout_hours": self.IDLE_HOURS, "back": "/security#idle"},
                         environ_base=x.E)
            assert r.status_code == 302, r.status_code
        else:
            assert fields.get("main_posted") == ["1"] and "idle_signout_hours" in fields, fields
            fields.update(idle_signout_hours=[self.IDLE_HOURS], check_in_snooze_months=[self.CHECK_IN_MONTHS],
                          home_base=[self.HOME_BASE])
            if on_general:
                fields.update(mileage_rate=[self.MILEAGE_RATE])
            r = x.c.post("/settings", data=fields, environ_base=x.E)
            assert r.status_code == 302 and "saved=1" in r.headers["Location"], r.headers.get("Location")
        if not on_general:
            page_ = x.get("/setup/money").get_data(as_text=True)
            money_ = form_fields(page_, action="/setup/money") if rebuilt else form_fields(page_)
            assert "mileage_rate" in money_, money_
            money_.update(mileage_rate=[self.MILEAGE_RATE])
            r = x.c.post("/setup/money", data=money_, environ_base=x.E)
            assert r.status_code == 302, (r.status_code, r.get_data(as_text=True)[:400])
        for k, v in (("idle_signout_hours", self.IDLE_HOURS), ("check_in_snooze_months", self.CHECK_IN_MONTHS),
                     ("mileage_rate", self.MILEAGE_RATE)) + (() if rebuilt else (("home_base", self.HOME_BASE),)):
            got = one("SELECT value FROM settings WHERE key=?", (k,))[0]
            assert (float(got) == float(v)) if k == "mileage_rate" else got == v, (k, got)
        assert x.in_request(x.A.app.config["idle_seconds"]) == int(self.IDLE_HOURS) * 3600

        post("/security/notice", act="save", **self.NTFY)
        assert {k: one("SELECT value FROM settings WHERE key=?", ("ntfy_" + k,))[0] for k in self.NTFY} == self.NTFY
        plain = json.loads(Path(x.work, "ntfy.json").read_text())
        assert {k: plain[k] for k in self.NTFY} == self.NTFY and plain["tz"] == TIME_ZONE, plain
        assert "Set up the locked-studio notice" in x.get("/settings?tab=notifications" if rebuilt else "/security").get_data(as_text=True) \
            or rebuilt

        import wording
        self.release_text = wording.STANDARD["release"].rstrip() + "\n\n" + self.RELEASE_ADDED
        post("/settings/wording/release/here", text=self.release_text, back="/settings?tab=documents")
        saved = one("SELECT value FROM settings WHERE key='release_text'")[0]
        assert self.RELEASE_ADDED in saved, saved[-200:]
        assert "release:*" in json.loads(one("SELECT value FROM settings WHERE key='doc_reviews'")[0])

        cur = x.in_request(lambda: x.A.current_schedule()["id"])
        post(f"/fees/{cur}/approve")
        assert str(cur) in json.loads(one("SELECT value FROM settings WHERE key='price_approvals'")[0])

    def _zeller(self, x):
        post, one = x.post, x.one
        post("/contact/new", contact_name="Jo Zeller", household_name="Zeller", email="zeller@example.com")
        hid = one("SELECT id FROM households WHERE name='Zeller'")[0]
        post(f"/client/{hid}/session", type="family", stage="inquiry", title="Zeller family", shoot_date=f"{YEAR}-10-24")
        sid = one("SELECT MAX(id) FROM sessions")[0]
        stage = lambda: one("SELECT stage FROM sessions WHERE id=?", (sid,))[0]
        post(f"/session/{sid}/money", what="line", label="Family session", amount="300")
        post(f"/session/{sid}/quote/send")
        assert stage() == "inquiry", stage()
        vid = one("SELECT MAX(id) FROM quote_versions WHERE session_id=?", (sid,))[0]
        post(f"/quote/{vid}/sent")
        assert stage() == "quoted", stage()
        post(f"/session/{sid}/quick", act="book")
        assert stage() == "booked", stage()
        post(f"/session/{sid}/money", what="payment", kind="deposit", amount="100", method="other",
             note="Two framed prints in trade", received=f"{YEAR}-10-01")
        assert one("SELECT note FROM payments WHERE session_id=?", (sid,))[0] == "Two framed prints in trade"
        post(f"/session/{sid}/quote/replace")
        assert one("SELECT 1 FROM quote_replacements WHERE session_id=? AND booked_at IS NULL", (sid,))
        post(f"/session/{sid}/money", what="line", label="Extra half hour", amount="75")
        post(f"/session/{sid}/quick", act="rebook")
        assert one("SELECT booked_at FROM quote_replacements WHERE session_id=?", (sid,))[0]
        post(f"/session/{sid}/quick", act="shot")
        assert stage() == "shot", stage()
        post(f"/session/{sid}/money", what="expense", label="Drive to the river park", miles="18", amount="",
             date=f"{YEAR}-10-24")
        trip = one("SELECT miles, rate FROM expenses WHERE session_id=?", (sid,))
        assert trip and tuple(trip) == (18, float(self.MILEAGE_RATE)), (trip and tuple(trip), x.rows("SELECT * FROM expenses"))
        self.s150["zeller"], self.h150["zeller"] = sid, hid

    def _not_booked(self, x):

        post, one = x.post, x.one
        post("/contact/new", contact_name="Pat Quinn", household_name="Quinn", email="quinn@example.com")
        hid = one("SELECT id FROM households WHERE name='Quinn'")[0]
        post(f"/client/{hid}/session", type="family", stage="inquiry", title="Quinn family", shoot_date=f"{YEAR}-12-19")
        s_quinn = one("SELECT MAX(id) FROM sessions")[0]
        post(f"/session/{s_quinn}/stage", stage="lost", need_reason="1", lost_reason="They went elsewhere")
        assert tuple(one("SELECT stage, lost_reason FROM sessions WHERE id=?", (s_quinn,))) == ("lost", "They went elsewhere")
        post("/contact/new", contact_name="Lee Abbott", household_name="Abbott", email="abbott@example.com")
        hid = one("SELECT id FROM households WHERE name='Abbott'")[0]
        post(f"/client/{hid}/session", type="headshot", stage="inquiry", title="Abbott headshots", shoot_date=f"{YEAR}-12-03")
        s_abbott = one("SELECT MAX(id) FROM sessions")[0]
        post(f"/session/{s_abbott}/money", what="line", label="Headshot session", amount="150")
        post(f"/session/{s_abbott}/quote/send")
        post(f"/quote/{one('SELECT MAX(id) FROM quote_versions WHERE session_id=?', (s_abbott,))[0]}/sent")
        post(f"/session/{s_abbott}/quick", act="book")
        assert one("SELECT stage FROM sessions WHERE id=?", (s_abbott,))[0] == "booked"
        post(f"/session/{s_abbott}/stage", stage="lost", need_reason="1", lost_reason="Other", lost_note="Moved out of state")
        assert tuple(one("SELECT stage, lost_reason FROM sessions WHERE id=?", (s_abbott,))) == ("lost", "Other")
        self.s150.update(quinn=s_quinn, abbott=s_abbott)

    def _clients150(self, x):
        post, one = x.post, x.one
        hids = dict(ellis=one("SELECT household_id FROM sessions WHERE id=?", (self.s130["ellis"],))[0],
                    foster=one("SELECT household_id FROM sessions WHERE id=?", (self.s130["foster"],))[0],
                    kim=one("SELECT id FROM households WHERE name='Kim'")[0],
                    vance=one("SELECT household_id FROM sessions WHERE id=?", (self.s141["vance"],))[0],
                    zeller=self.h150["zeller"])
        for who, label in self.LABELS:
            post(f"/client/{hids[who]}/labels", label=label)
        post(f"/client/{hids['foster']}/end-contact")
        post(f"/client/{hids['kim']}/check-in")
        assert one("SELECT 1 FROM log WHERE household_id=? AND summary='Checked in by email'", (hids["kim"],))
        page = x.get(f"/client/{hids['vance']}").get_data(as_text=True)
        fields = form_fields(page, f"/client/{hids['vance']}")
        assert "business_posted" in fields, sorted(fields)
        fields["business"] = ["1"]
        r = x.c.post(f"/client/{hids['vance']}", data=fields, environ_base=x.E)
        assert r.status_code == 302, r.status_code
        assert x.in_request(lambda: x.A.is_business_client(hids["vance"]))
        self.h150.update(hids)

    def _measure140(self, x, out):
        got = super()._measure140(x, out)
        return dict(got, **self._measure150(x, dict(out, **got)))

    def _measure150(self, x, out):
        import hashlib
        A, one, rows, in_request = x.A, x.one, x.rows, x.in_request
        keys = ("idle_signout_hours", "check_in_snooze_months", "mileage_rate", "home_base", "ntfy_server", "ntfy_topic",
                "ntfy_token", "doc_reviews", "price_approvals")
        new_sids = sorted(self.s150.values())
        history = {str(sid): [r[0] for r in rows("SELECT action FROM audit WHERE session_id=? ORDER BY id", (sid,))]
                   for sid in new_sids}
        return dict(
            sessions_by_name=dict(out["sessions_by_name"], **self.s150),
            settings_150={k: (one("SELECT value FROM settings WHERE key=?", (k,)) or [None])[0] for k in keys},
            release_text_sha256=hashlib.sha256(one("SELECT value FROM settings WHERE key='release_text'")[0].encode()).hexdigest(),
            release_added=self.RELEASE_ADDED,
            ntfy_json=json.loads(Path(x.work, "ntfy.json").read_text()),
            account_activity=[dict(text=r_["text"], who=r_["who"]) for r_ in in_request(A.account_activity)],
            labels=sorted([r[0], r[1]] for r in rows("SELECT household_id, label FROM client_labels")),
            end_contact=[list(r) for r in rows("SELECT household_id, since FROM client_end_contact ORDER BY household_id")],
            check_ins=[list(r) for r in rows("SELECT household_id, channel, summary FROM log WHERE summary='Checked in by email'"
                                             " ORDER BY id")],
            business_clients={str(r[0]): r[1] for r in rows("SELECT id, business FROM households WHERE business IS NOT NULL")},
            clients_150={k: v for k, v in self.h150.items()},
            lost_150={str(r[0]): [r[1], r[2]] for r in rows(
                "SELECT id, stage, lost_reason FROM sessions WHERE id IN (?, ?)", (self.s150["quinn"], self.s150["abbott"]))},
            history_150=history,
            quote_replacements=[dict(session=r[0], old_total=r[1], old_vid=r[2], new_vid=r[3], booked=bool(r[4]))
                                for r in rows("SELECT session_id, old_total, old_vid, new_vid, booked_at FROM quote_replacements"
                                              " ORDER BY id")],
            payment_notes={str(r[0]): r[1] for r in rows("SELECT id, note FROM payments WHERE note != '' ORDER BY id")},
            expenses=[list(r) for r in rows("SELECT session_id, date, label, amount, miles, rate FROM expenses ORDER BY id")],
        )

def scenario_150(x, made_with):
    x.v15 = True
    return scenario_101(x, made_with, more=More150())

class More154(More150):

    WEDDING = "Wynn and Sato"
    SCHOOL = "Maple Grove Elementary picture day"
    SCHOOL_DATE = f"{YEAR}-10-08"
    FAMILY_PRICE = "45"

    FAMILIES = (("Abara", "Nia Abara", "Kofi 7"), ("Brandt", "Lena Brandt", "Max 9, Ida 7 and Paul 5"),
                ("Castillo", "Marta Castillo", "Luis 8 and Ana 10"), ("Dunmore", "Pat Dunmore", "Finn 11"),
                ("Eklund", "Siri Eklund", "Elsa 7 and Nils 9"), ("Farrow", "Jamie Farrow", "June 6"),
                ("Gupta", "Asha Gupta", "Dev 8"))
    SIGNED_AT_EVENT = ("Abara", "Brandt", "Castillo", "Eklund", "Gupta")
    PAID = ("Abara", "Brandt", "Dunmore", "Gupta")

    def data(self, x):
        super().data(x)
        self.s154, self.h154, self.e154 = {}, {}, {}
        self.types154(x)
        self.wedding(x)
        self.school(x)

    def types154(self, x):

        have = x.in_request(lambda: list(x.A.C.SESSION_TYPES))
        x.post("/setup/shoot", preset_weddings="1", preset_engagements="1", preset_events="1",
               **{f"keep_{k}": "1" for k in have})
        now = x.in_request(lambda: list(x.A.C.SESSION_TYPES))
        assert set(have) | {"wedding", "engagement", "event"} <= set(now), (have, now)

    def wedding(self, x):
        post, one, rows = x.post, x.one, x.rows
        post("/contact/new", contact_name="Robin Wynn", household_name=self.WEDDING, email="robin.wynn@example.com")
        hid = one("SELECT id FROM households WHERE name=?", (self.WEDDING,))[0]
        post(f"/client/{hid}/person", name="Alex Sato", role="adult", email="alex.sato@example.com")
        post(f"/client/{hid}/person", name="Ivy Wynn", role="child", age="8")
        post(f"/client/{hid}/person", name="Theo Sato", role="child", age="10")
        pid = {r[1]: r[0] for r in rows("SELECT id, name FROM people WHERE household_id=?", (hid,))}
        couple = [pid["Robin Wynn"], pid["Alex Sato"]]
        party = [pid["Ivy Wynn"], pid["Theo Sato"]]

        post(f"/client/{hid}/session", type="wedding", stage="inquiry", title="Wynn and Sato wedding",
             shoot_date=f"{YEAR}-09-19", subjects=[str(p) for p in couple + party])
        s_wed = one("SELECT MAX(id) FROM sessions")[0]
        post(f"/client/{hid}/session", type="engagement", stage="inquiry", title="Wynn and Sato engagement",
             shoot_date=f"{YEAR}-05-09", subjects=[str(p) for p in couple])
        s_eng = one("SELECT MAX(id) FROM sessions")[0]
        post(f"/session/{s_eng}/plan", plan_of=str(s_wed))
        assert one("SELECT plan_of FROM sessions WHERE id=?", (s_eng,))[0] == s_wed
        assert not one("SELECT 1 FROM documents WHERE session_id=? AND kind='agreement'", (s_eng,))
        signers = rows("SELECT id, about_person FROM documents WHERE session_id=? AND kind='agreement' ORDER BY id", (s_wed,))
        assert sorted(r[1] for r in signers) == sorted(couple), signers
        releases = rows("SELECT id, about_person FROM documents WHERE session_id=? AND kind='release' ORDER BY id", (s_wed,))
        assert sorted(r[1] for r in releases) == sorted(party), releases

        post(f"/session/{s_wed}/delivery", delivery_method="link")
        post(f"/session/{s_wed}/money", what="line", label="Wedding day (8 hr)", amount="2800")
        post(f"/session/{s_wed}/money", what="line", label="Engagement session (1.5 hr)", amount="350")
        post(f"/session/{s_wed}/quote/send")
        post(f"/quote/{one('SELECT MAX(id) FROM quote_versions WHERE session_id=?', (s_wed,))[0]}/sent")
        assert one("SELECT stage FROM sessions WHERE id=?", (s_wed,))[0] == "quoted"
        post(f"/session/{s_eng}/docs/sent", kind="agreement")
        assert {r[0] for r in rows("SELECT status FROM documents WHERE session_id=? AND kind='agreement'", (s_wed,))} == {"sent"}
        for did, _ in signers:
            post(f"/session/{s_wed}/quick", act="sign", kind="agreement", did=str(did))
        assert {r[0] for r in rows("SELECT status FROM documents WHERE session_id=? AND kind='agreement'", (s_wed,))} == {"signed"}
        post(f"/session/{s_wed}/quick", act="book")
        post(f"/session/{s_wed}/money", what="payment", kind="deposit", amount="945", method="check", received=f"{YEAR}-03-02")
        post(f"/session/{s_wed}/docs/sent", kind="release")
        post(f"/session/{s_wed}/quick", act="sign", kind="release", did=str(releases[0][0]))
        post(f"/session/{s_eng}/quick", act="book")
        post(f"/session/{s_eng}/quick", act="shot")
        stages = {r[0]: r[1] for r in rows("SELECT id, stage FROM sessions WHERE id IN (?, ?)", (s_wed, s_eng))}
        assert stages == {s_wed: "booked", s_eng: "shot"}, stages
        self.s154.update(wedding=s_wed, engagement=s_eng)
        self.h154["wedding"] = hid

    def school(self, x):
        post, one, rows = x.post, x.one, x.rows
        post("/events", name=self.SCHOOL, date=self.SCHOOL_DATE, venue="Maple Grove Elementary gym")
        eid = one("SELECT MAX(id) FROM events")[0]
        post(f"/event/{eid}/billing", billing="families")
        for name, parent, children in self.FAMILIES:
            post(f"/event/{eid}/family", household_name=name, parent_name=parent, children=children,
                 email=f"{name.lower()}@example.com")
        post(f"/event/{eid}/family-price", amount=self.FAMILY_PRICE)
        fam = {r[1]: r[0] for r in rows("""SELECT s.id, h.name FROM sessions s JOIN households h ON h.id=s.household_id
                                          WHERE s.event_id=?""", (eid,))}
        assert sorted(fam) == sorted(f[0] for f in self.FAMILIES), fam
        n_rel = one("""SELECT COUNT(*) FROM documents WHERE kind='release' AND session_id IN
                       (SELECT id FROM sessions WHERE event_id=?)""", (eid,))[0]
        assert n_rel == 11, n_rel
        for name in self.SIGNED_AT_EVENT:
            post(f"/event/{eid}/releases", session_id=str(fam[name]), signed_on=self.SCHOOL_DATE)
        for name in self.PAID:
            due = x.in_request(lambda sid=fam[name]: x.A.totals(sid))["due"]
            post(f"/session/{fam[name]}/money", what="payment", kind="balance", amount=f"{due:.2f}",
                 method="cash", received=self.SCHOOL_DATE)
            assert x.in_request(lambda sid=fam[name]: x.A.totals(sid))["due"] <= 0.005, name
        post(f"/event/{eid}/stage", stage="shot")
        assert {r[0] for r in rows("SELECT stage FROM sessions WHERE event_id=?", (eid,))} == {"shot"}
        self.e154["school"] = eid
        self.s154.update({f"school_{n.lower()}": s for n, s in fam.items()})

    def _measure140(self, x, out):
        got = super()._measure140(x, out)
        return dict(got, **self._measure154(x, dict(out, **got)))

    def _measure154(self, x, out):
        one, rows, in_request, A = x.one, x.rows, x.in_request, x.A
        s_wed, s_eng, eid = self.s154["wedding"], self.s154["engagement"], self.e154["school"]
        fam = sorted((k, v) for k, v in self.s154.items() if k.startswith("school_"))
        docs = lambda sid, kind: [dict(person=r[0], status=r[1]) for r in rows(
            """SELECT p.name, d.status FROM documents d LEFT JOIN people p ON p.id=d.about_person
               WHERE d.session_id=? AND d.kind=? ORDER BY d.id""", (sid, kind))]
        return dict(
            sessions_by_name=dict(out["sessions_by_name"], **self.s154),
            wedding=dict(client=self.h154["wedding"], name=self.WEDDING,
                         people=[list(r) for r in rows("SELECT name, role, is_minor FROM people WHERE household_id=? ORDER BY id",
                                                       (self.h154["wedding"],))],
                         plan={str(s_eng): s_wed}, agreement=docs(s_wed, "agreement"), releases=docs(s_wed, "release"),
                         stages={str(r[0]): r[1] for r in rows("SELECT id, stage FROM sessions WHERE id IN (?, ?)", (s_wed, s_eng))},
                         delivery_method=one("SELECT delivery_method FROM sessions WHERE id=?", (s_wed,))[0],
                         totals={str(s): {k: round(v, 2) for k, v in in_request(lambda s=s: A.totals(s)).items()
                                          if k in ("quoted", "paid", "due")} for s in (s_wed, s_eng)}),
            school=dict(event=eid, name=self.SCHOOL, date=self.SCHOOL_DATE, family_price=float(self.FAMILY_PRICE),
                        families={k[len("school_"):]: dict(
                            session=sid, children=[r[0] for r in rows(
                                """SELECT p.name FROM people p JOIN session_people sp ON sp.person_id=p.id
                                   WHERE sp.session_id=? ORDER BY p.id""", (sid,))],
                            releases=[r[0] for r in rows(
                                "SELECT status FROM documents WHERE session_id=? AND kind='release' ORDER BY id", (sid,))],
                            due=round(in_request(lambda sid=sid: A.totals(sid))["due"], 2)) for k, sid in fam}),
        )

def scenario_154(x, made_with):

    x.v15 = True
    return scenario_101(x, made_with, more=More154())

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

    "1.3.1": dict(commit="40adae6", scenario=scenario_131, files=(), dirs=("galleries", "galleries-kept", "host", "web", "photos"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.4.0": dict(commit="c45ce75", scenario=scenario_140, files=(),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.4.1": dict(commit="879c2b0", scenario=scenario_141, files=(),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),
    "1.5.0": dict(commit="48507a6", scenario=scenario_150, files=("ntfy.json",),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.5.1": dict(commit="02dfe7c", scenario=scenario_150, files=("ntfy.json",),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.5.2": dict(commit="31b64af", scenario=scenario_150, files=("ntfy.json",),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),
    "1.5.3": dict(commit="1b36647", scenario=scenario_150, files=("ntfy.json",),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.5.4": dict(commit="02128b8", scenario=scenario_154, files=("ntfy.json",),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.6.0": dict(commit="5f7dbe6", scenario=scenario_154, files=("ntfy.json",),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.6.1": dict(commit="618def1", scenario=scenario_154, files=("ntfy.json",),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.6.2": dict(commit="4fe66e5", scenario=scenario_154, files=("ntfy.json",),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "1.6.3": dict(commit="aa7c3aa", scenario=scenario_154, files=("ntfy.json",),
                  dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
                  env=dict(CRM_PHOTO_FOLDERS="1", CRM_GALLERY_ROOT="galleries")),

    "next": dict(commit="HEAD", scenario=scenario_154, files=("ntfy.json",),
                 dirs=("galleries", "galleries-kept", "host", "web", "photos", "faces"),
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
    if version == "next":
        if "--out" not in args:
            sys.exit("--version next needs --out <folder> (an unreleased build is never saved under tests/fixtures)")
        args.setdefault("--code", str(REPO))
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
