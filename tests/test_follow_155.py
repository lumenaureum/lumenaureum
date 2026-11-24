import os
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
SO = {"Sec-Fetch-Site": "same-origin"}

def row(A, sql, args=()):
    con = A.connect()
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()

import test_129_155 as T129
import test_client_page_141 as T141

def _raw(fx):

    w_ = getattr(fx, "__pytest_wrapped__", None)
    return w_.obj if w_ is not None else fx._get_wrapped_function()

@pytest.fixture
def card_env(tmp_path, monkeypatch):
    return _raw(T129.env)(tmp_path, monkeypatch)

@pytest.fixture
def page_env(tmp_path, monkeypatch):
    return _raw(T141.env)(tmp_path, monkeypatch)

def test_saved_card_zip_asks_card_handed_over(card_env):
    T = T129
    A, c, G, tmp, src = card_env
    sid = T.session(A, delivery_method="card", stage="delivered")
    c.post(f"/session/{sid}/gallery/card", data={"to": "zip", "source_dir": str(src)}, environ_base=E)
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Card handed over" not in page
    r = c.get(f"/session/{sid}/gallery/card.zip", environ_base=E, headers=SO)
    r.get_data(); r.close()
    assert not row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0]
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Card handed over?" in page and ">Card handed over</button>" in page
    assert "Card zip saved" in page
    code = row(A, "SELECT code FROM gallery_cards WHERE session_id=?", (sid,))[0]
    assert code not in page and code.replace("-", "") not in page
    c.post(f"/session/{sid}/deliver/another", data={"how": "card", "step": "sd_shipped"}, environ_base=E)
    assert row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0] == date.today().isoformat()
    assert "sealed card" in row(A, "SELECT note FROM step_closes WHERE session_id=? AND step='sd_shipped'", (sid,))[0]
    page = c.get(f"/c/1?s={sid}", environ_base=E).get_data(as_text=True)
    assert "Card handed over?" not in page

def test_writing_to_the_card_still_delivers_at_once(card_env):
    T = T129
    A, c, G, tmp, src = card_env
    sid = T.session(A, delivery_method="card")
    T.write_card(c, sid, src, tmp / "mount")
    assert row(A, "SELECT sd_shipped FROM sessions WHERE id=?", (sid,))[0] == date.today().isoformat()

def _book(A, c, **form):
    return c.post("/session/1/quick", data=dict(act="book", back="/c/1?s=1", **form), environ_base=E)

def test_book_it_asks_is_this_the_final_date_ticked(page_env):
    A, c = page_env
    T141._session(A, "quoted", days=20)
    with A.app.test_request_context():
        day = A.nice((A.today() + timedelta(days=20)).isoformat())
    assert f'name="final" value="1" checked>Is {day} the final date?</label>' in T141._page(c)
    today_ = c.get("/", environ_base=E).get_data(as_text=True)
    assert f"Is {day} the final date?" in today_
    con = A.connect(); con.execute("UPDATE sessions SET date_tentative=1 WHERE id=1"); con.commit(); con.close()
    _book(A, c, final_shown="1", final="1")
    assert tuple(row(A, "SELECT stage, date_tentative FROM sessions WHERE id=1")) == ("booked", 0)

def test_unticked_keeps_date_not_final_yet(page_env):
    A, c = page_env
    T141._session(A, "quoted", days=20)
    _book(A, c, final_shown="1")
    assert tuple(row(A, "SELECT stage, date_tentative FROM sessions WHERE id=1")) == ("booked", 1)

def test_book_without_the_box_leaves_the_date_as_it_was(page_env):
    A, c = page_env
    T141._session(A, "quoted", days=20)
    con = A.connect(); con.execute("UPDATE sessions SET date_tentative=1 WHERE id=1"); con.commit(); con.close()
    _book(A, c)
    assert tuple(row(A, "SELECT stage, date_tentative FROM sessions WHERE id=1")) == ("booked", 1)

def test_book_it_with_no_date_has_no_box(page_env):
    A, c = page_env
    T141._session(A, "quoted", days=None)
    assert "the final date?" not in T141._page(c)

def test_upload_with_face_photos_file(tmp_path):
    import test_158_155 as T158
    vault = tmp_path / "vault"
    data = T158._install(tmp_path, str(vault))
    out = T158.run(T158.SETUP + """
import io
V = VAULT
def blob(n): return open(os.path.join(V, n), 'rb').read()
def check(db, faces=None):
    d = {'go': 'check', 'secret': secret, 'file': (io.BytesIO(blob(db)), 'backup.age')}
    if faces is not None:
        d['faces'] = (io.BytesIO(faces), 'faces.age')
    return c.post('/vault/restore', data=d, content_type='multipart/form-data', **ip())
sql("INSERT INTO households (name) VALUES ('Client A')")
A.FACES.mkdir(parents=True, exist_ok=True); (A.FACES / '1.jpg').write_bytes(b'face')
one = BK.sealed_backup(str(A.DB_PATH), str(A.FACES), kind='manual')
(A.FACES / '2.jpg').write_bytes(b'face two')
two = BK.sealed_backup(str(A.DB_PATH), str(A.FACES), kind='manual')
print('names', len(one), len(two))
page = c.get('/vault/restore').get_data(as_text=True)
print('picker', 'Face photos backup (if you have it)' in page, 'name="faces"' in page)
r = check(one[0], blob(two[1]))                                 # the faces file of another run: refused
print('mismatch', r.status_code, 'its fingerprint differs' in r.get_data(as_text=True), not R.stage_dir().exists())
r = check(one[0], b'not a faces file')                         # recorded: anything else is a mismatch
print('junk faces', r.status_code, 'its fingerprint differs' in r.get_data(as_text=True))
from werkzeug.datastructures import FileStorage                 # no record (a new install): it does not open, faces stay
tok = R.check('upload', secret, schema_now=99, fresh=True, upload=FileStorage(io.BytesIO(blob(one[0])), 'b.age'),
              faces_upload=FileStorage(io.BytesIO(b'not a faces file'), 'f.age'))
print('unrecorded junk', R.PENDING[tok]['faces'])
tok = R.check('upload', secret, schema_now=99, fresh=True, upload=FileStorage(io.BytesIO(blob(one[0])), 'b.age'),
              faces_upload=FileStorage(io.BytesIO(blob(one[1])), 'f.age'))
print('unrecorded faces', R.PENDING[tok]['faces'])
R.forget()
r = check(one[0])
rv = c.get('/vault/restore?step=review').get_data(as_text=True)
print('no faces', 'No face photos file was chosen' in rv)
r = check(one[0], blob(one[1]))
rv = c.get('/vault/restore?step=review').get_data(as_text=True)
print('faces', r.status_code, 'The face photos come back from the backup too.' in rv)
r = c.post('/vault/restore', data={'go': 'restore', 'password': TEST_PASSWORD}, **ip())
print('done', r.status_code)
signin()
print('restored faces', sorted(os.listdir(A.FACES)))
""".replace("VAULT", repr(str(vault))), data)
    assert "names 2 2" in out and "picker True True" in out, out
    assert "mismatch 400 True True" in out, out
    assert "junk faces 400 True" in out and "no faces True" in out, out
    assert "unrecorded junk upload-unopened" in out and "unrecorded faces restored" in out, out
    assert "faces 302 True" in out and "done 200" in out and "restored faces ['1.jpg']" in out, out

@pytest.fixture
def eq_env(tmp_path, monkeypatch):
    import test_155_155 as T155
    return _raw(T155.env)(tmp_path, monkeypatch)

def test_renewal_stays_until_updated_and_is_late_from_the_day(eq_env):
    import test_155_155 as T155
    A, c = eq_env[0], eq_env[1]
    T155.turn_on(A)
    long_ago, today_, soon = ((A.today() + timedelta(days=d)).isoformat() for d in (-90, 0, 10))
    T155.add(c, {"name": "Camera body", "renews": long_ago})
    T155.add(c, {"name": "Flash", "renews": today_})
    T155.add(c, {"name": "Lens", "renews": soon})
    html = c.get("/", environ_base=E, follow_redirects=True).get_data(as_text=True)
    t = T155.text(c, "/")
    assert "Camera body: lapsed on" in t and "Flash: lapsed on" in t and "Lens: renews on" in t
    assert html.count('ui-chip ui-chip-late">Late<') >= 2
    lens = html[html.index("Lens: renews on"):][:300]
    assert "ui-chip-late" not in lens
    eid = T155.sql(A, "SELECT id FROM equipment WHERE name='Camera body'")[0][0]
    c.post(f"/equipment/{eid}", data={"name": "Camera body", "renews": (A.today() + timedelta(days=300)).isoformat()},
           content_type="multipart/form-data", environ_base=E)
    assert "Camera body" not in T155.text(c, "/")

def _pay(c, day, kind="deposit", amount="50"):
    return c.post("/session/1/money", data={"what": "payment", "kind": kind, "amount": amount, "method": "Cash",
                                            "received": day, "back": "/c/1?s=1"}, environ_base=E)

def _n_payments(A):
    return row(A, "SELECT COUNT(*) FROM payments WHERE session_id=1")[0]

def test_single_payment_refuses_a_closed_earlier_year_with_the_bulk_words(page_env, monkeypatch):
    A, c = page_env
    monkeypatch.setattr(A, "books_open_from", lambda: f"{A.today().year}-01-01")
    T141._session(A, "booked", days=20)
    last_year = f"{A.today().year - 1}-12-30"
    _pay(c, last_year)
    assert _n_payments(A) == 0
    with A.app.test_request_context():
        words = A.closed_year_refusal(last_year)
    y_ = last_year[:4]
    assert words == f"That date is in {y_}, and {y_} is closed: the app records nothing more in a closed year. Nothing was recorded."
    import html as _h
    assert words in _h.unescape(T141._page(c))
    _pay(c, "", kind="tip", amount="20")
    assert _n_payments(A) == 1
    c.post("/session/1/money", data={"what": "payment", "kind": "tip_back", "amount": "5", "method": "Cash",
                                     "received": last_year, "back": "/c/1?s=1"}, environ_base=E)
    assert _n_payments(A) == 1

def test_an_open_earlier_year_still_accepts(page_env, monkeypatch):
    A, c = page_env
    T141._session(A, "booked", days=20)
    last_year = f"{A.today().year - 1}-12-30"
    monkeypatch.setattr(A, "books_open_from", lambda: f"{A.today().year - 1}-01-01")
    _pay(c, last_year)
    assert _n_payments(A) == 1
