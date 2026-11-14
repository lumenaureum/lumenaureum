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

def scenario_101(x, made_with):

    A, work, post, get, one, rows, in_request = x.A, x.work, x.post, x.get, x.one, x.rows, x.in_request
    post("/settings/timezone", timezone=TIME_ZONE)
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
    return expected

VERSIONS = {
    "1.0.0": dict(commit="a0966c9", scenario=scenario_100, files=("crm-before-delete.db",), env={}),
    "1.0.1": dict(commit="v1.0.1", scenario=scenario_101, files=(),
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
        made = code = tempfile.mkdtemp(prefix=f"lumen-{version.replace('.', '')}-")
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
            for extra in sorted(os.listdir(work)):
                print("  (in the data folder:", extra + ")")
    finally:
        if made:
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", made], check=False)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
