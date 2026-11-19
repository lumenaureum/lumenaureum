import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from helpers import fresh_modules, signed_in_client
from helpers_tools import need_tool
from test_orders_php import host

E = {"REMOTE_ADDR": "127.0.0.1"}

def _q(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()

@pytest.fixture
def fresh(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries"), ("CRM_SITE_ROOT", "site")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    fresh_modules()
    import app as A
    import setup
    monkeypatch.setattr(setup, "same_disk", lambda a, b: False)
    c = signed_in_client(A, setup_done=False)
    con = A.connect()
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('setup_done', ?)",
                (json.dumps(["business", "documents", "money", "shoot", "storage", "tax"]),))
    con.commit()
    con.close()
    A.app.config["gallery_start_upgrade"] = lambda: None
    return A, c

@pytest.fixture
def self_signed(tmp_path):
    from test_web_cert_pin import make_cert, serve
    d = tmp_path / "certs"
    d.mkdir()
    certs = {"self": make_cert(d, "self", names=("127.0.0.1",))}
    f = serve(certs, "self")
    yield f, certs["self"][0]
    f.close()

def _galleries_form(tmp_path, addr):
    web = tmp_path / "web" / "g"
    web.mkdir(parents=True)
    return dict(gallery_type="local", gallery_path=str(web), gallery_init="1", gallery_days="30", unopened_days="14",
                gallery_base_url=f"https://{addr}/g/")

def test_the_web_host_certificate_is_trusted_inside_first_run_setup(fresh, self_signed, tmp_path):
    import webtls
    from test_web_cert_pin import sha_of
    A, c = fresh
    f, cert = self_signed
    addr = f"127.0.0.1:{f.port}"
    r = c.post("/setup/galleries", data=_galleries_form(tmp_path, addr), environ_base=E)
    assert r.status_code == 302 and "/setup/galleries" in r.headers["Location"] and "#webcert" in r.headers["Location"]
    page = c.get("/setup/galleries", environ_base=E).get_data(as_text=True)
    assert "Trust this certificate" in page and webtls.colon_hex(sha_of(cert)) in page
    assert webtls.pins() == {}
    r = c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(cert)}"}, environ_base=E)
    assert webtls.pins()[addr]["sha256"] == sha_of(cert)
    assert r.status_code == 302 and "/setup/galleries" in r.headers["Location"] and "#hostcaps" in r.headers["Location"]
    page = c.get(r.headers["Location"].split("#")[0].replace("https://localhost", ""), environ_base=E).get_data(as_text=True)
    assert "What this host can do" in page
    assert 'href="/setup/network">Go on to the next step' in page
    assert _q(A, "SELECT COUNT(*) FROM audit WHERE action LIKE 'Trusted the web host''s certificate at %'")[0][0] == 1
    r = c.post("/setup/network", data={}, environ_base=E)
    assert _q(A, "SELECT value FROM settings WHERE key='setup_complete'")[0][0] == "1"
    today = c.get("/", environ_base=E).get_data(as_text=True)
    assert "isn&#39;t trusted" not in today and "#webcert" not in today
    assert webtls.pins()[addr]["sha256"] == sha_of(cert)

def test_the_check_button_works_inside_first_run_setup(fresh, self_signed, tmp_path):
    import webtls
    A, c = fresh
    f, _cert = self_signed
    addr = f"127.0.0.1:{f.port}"
    c.post("/setup/galleries", data=_galleries_form(tmp_path, addr), environ_base=E)
    r = c.post("/setup/web-cert/check", data={"addr": addr}, environ_base=E)
    assert r.status_code == 302 and "/setup/galleries" in r.headers["Location"] and "#webcert" in r.headers["Location"]
    r = c.post("/setup/web-cert/refuse", data={"addr": addr}, environ_base=E)
    assert "/setup/galleries" in r.headers["Location"]
    assert webtls.pins() == {}

def test_only_a_signed_in_owner_can_trust_it_during_setup(fresh, self_signed, tmp_path):
    import webtls
    from helpers import HttpsClient
    from test_web_cert_pin import sha_of
    A, c = fresh
    f, cert = self_signed
    addr = f"127.0.0.1:{f.port}"
    c.post("/setup/galleries", data=_galleries_form(tmp_path, addr), environ_base=E)
    A.app.test_client_class = HttpsClient
    stranger = A.app.test_client()
    r = stranger.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(cert)}"}, environ_base=E)
    assert r.status_code in (302, 303) and "/login" in r.headers["Location"]
    assert webtls.pins() == {}

    r = c.post("/setup/web-cert/trust", data={"cert": f"{addr} {sha_of(cert)}"}, environ_base=E,
               headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code >= 400 and webtls.pins() == {}

def _files(web):
    return sorted(str(p.relative_to(web)) for p in web.rglob("*")
                  if p.is_file() and not str(p.relative_to(web)).startswith("g/"))

def test_the_upgraded_studio_page_has_every_file_a_fresh_1_4_one_has(tmp_path, monkeypatch):
    php = need_tool("php")
    import test_upgrade_from_131 as T
    from test_upgrade_140_fixtures import _rewrite, _serve
    T._place(tmp_path)
    web = tmp_path / "web"
    assert not (web / "inquiries" / "index.html").exists()
    srv, port = _serve(php, web)
    try:
        _rewrite(monkeypatch, [("https://photos.example.com/", f"http://127.0.0.1:{port}/")])
        A, c, _taken = T._open(tmp_path, monkeypatch)[:3]
        ns = A.app.view_functions["studio_page"].__globals__
        monkeypatch.setitem(ns, "clear_build", lambda n: None)
        _q(A, "SELECT 1")
        con = A.connect()
        con.execute("DELETE FROM settings WHERE key='upgrade_140'")
        con.commit()
        con.close()
        with A.app.app_context():
            A.app.config["gallery_upgrade_run"]()
            A.app.config["gallery_upgrade_run"]()
        st = json.loads(_q(A, "SELECT value FROM settings WHERE key='upgrade_140'")[0][0])
        assert st.get("site") == "done", st
        assert (web / "inquiries" / "index.html").read_bytes() == b""
        fresh_ = ns["SITE_ROOT"] / "upgrade"

        new_15 = {"favicon.ico", "apple-touch-icon.png", "llms.txt"}
        assert _files(web) == [n for n in _files(fresh_) if n not in new_15]
        icon = (b'<link rel="icon" href="{r}favicon.ico" sizes="any">\n'
                b'<link rel="apple-touch-icon" href="{r}apple-touch-icon.png">\n')

        for n in [n_ for n_ in _files(web) if n_.startswith(("inquir", "contact/")) or n_ in ("sealed.php", "assets/inquiry.js")]:
            want = (fresh_ / n).read_bytes()
            for r_ in (b"", b"../", b"/"):
                want = want.replace(icon.replace(b"{r}", r_), b"")
            assert (web / n).read_bytes() in ((fresh_ / n).read_bytes(), want), n
        owned = {r[0] for r in _q(A, "SELECT name FROM studio_files")}
        assert "inquiries/index.html" in owned
    finally:
        srv.terminate()
        srv.wait(timeout=10)

def test_a_print_order_payment_over_the_balance_asks_and_says_so(tmp_path, monkeypatch):
    import prints_env as PE
    from test_print_orders import invoiced, orders, pay
    e = PE.make(tmp_path, monkeypatch)
    oid, total = invoiced(e)
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).get_data(as_text=True)

    form = re.search(r'<form method="post" action="/prints/order/%d/payment"[^>]*>' % oid, page).group(0)
    assert f'data-balance="{total / 100:.2f}"' in form and 'data-credit-on="order"' in form
    ui = (ROOT / "static" / "ui.js").read_text()
    assert "more than the balance. Record it anyway? The extra shows as a credit on the ' + on + '.'" in ui
    r = pay(e, oid, f"{(total + 500) / 100:.2f}")
    assert r.status_code == 302 and orders(e)[0]["state"] == "paid"
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).get_data(as_text=True)
    assert "Recorded. That&#39;s $5.00 more than the balance, so the order now shows a $5.00 credit to give back." in page \
        or "Recorded. That's $5.00 more than the balance, so the order now shows a $5.00 credit to give back." in page
    pay(e, oid, "1.00")
    page = e.c.get(f"/prints/order/{oid}", environ_base=E).get_data(as_text=True)
    assert "$6.00 credit" in page

def test_orders_php_says_when_the_hourly_limit_lifts(host):
    import test_orders_php as TO
    base, d, proof, G, key, spk = host
    u = base + "orders.php"
    for i in range(5):
        assert TO.call(u, TO.body(TO.order(G, key, spk, f"{i + 1:032x}")), proof)[0] == 204
    st, _b, h = TO.call(u, TO.body(TO.order(G, key, spk, "a" * 32)), proof)
    assert st == 429 and 3500 <= int(h["Retry-After"]) <= 3601

    t = d / "orders" / "tries.php"
    data = json.loads(t.read_text().split("\n", 1)[1])
    who = next(iter(data))
    fake = [f"{i:032x}.las" for i in range(100, 110)]
    for n in fake:
        (d / "orders" / n).write_bytes(b"x")
    data[who] = {"t": [], "w": fake}
    t.write_text("<?php exit; ?>\n" + json.dumps(data) + "\n")
    st, _b, h = TO.call(u, TO.body(TO.order(G, key, spk, "b" * 32)), proof)
    assert st == 429 and not h.get("Retry-After")

def test_the_rate_limit_reads_as_a_limit_not_an_outage():
    from test_viewer_prints import keys, run
    _priv, spk = keys()
    r = run({"hourly": {"statuses": [{"s": 429, "h": {"Retry-After": "1200"}}]},
             "waiting": {"statuses": [429]}}, spk)
    h = r["hourly"]["said"][-1]
    assert "You've sent several orders in the last hour. Please try again after " in h
    assert re.search(r"after \d{1,2}:\d{2}", h) and "could not take your order" not in h
    w = r["waiting"]["said"][-1]
    assert "You've sent several orders in a short time. Please try again later." in w
    assert "could not take your order" not in w
