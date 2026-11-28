import base64
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}
TOTP = "JBSWY3DPEHPK3PXP"

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.delenv("CRM_UNLOCK_KEY_FILE", raising=False)
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    return A, c, tmp_path

def code():
    import auth
    return auth.totp(TOTP)

def start(c, current=TEST_PASSWORD, code_=None):
    return c.post("/security", data=dict(act="newmaster", current=current, code=code() if code_ is None else code_),
                  environ_base=E)

def take_effect(c=None):

    import json
    from datetime import timedelta
    import app as A
    con = A.connect()
    try:
        v = json.loads(con.execute("SELECT value FROM settings WHERE key='master_pending'").fetchone()[0])
        v["due"] = (A.app.config["now"]() - timedelta(minutes=1)).isoformat(timespec="seconds")
        con.execute("UPDATE settings SET value=? WHERE key='master_pending'", (json.dumps(v),))
        con.commit()
    finally:
        con.close()
    assert A.app.config["master_tick"](force=True)
    if c is not None:
        import auth
        assert not auth.SESSIONS
        with c.session_transaction() as s:
            s["uid"] = 1
            s["sid"] = auth.start_session(1)

def shown_key(c):
    page = c.get("/security/new-master-key", environ_base=E).get_data(as_text=True)
    return re.search(r"AGE-SECRET-KEY-1[0-9A-Z]+", page).group(0), page

def test_old_key_stops_new_key_opens_password_still_does(env):
    A, c, _ = env
    import datakey
    old = c.master_secret
    before = datakey.dk()
    assert start(c).location.endswith("/security/new-master-key")
    new, page = shown_key(c)
    assert "data-print" in page and "data-copy" in page
    assert datakey.unwrap_master(old) == before
    r = c.post("/security/new-master-key", data=dict(tail=new[-6:]), environ_base=E)
    assert r.location.endswith("/security#master-pending")
    assert datakey.unwrap_master(old) == before
    take_effect()
    assert datakey.unwrap_master(new) == before
    with pytest.raises(ValueError):
        datakey.unwrap_master(old)
    assert datakey.unwrap_password(TEST_PASSWORD) == before
    con = A.connect()
    try:
        from vault import keys as K
        assert K.master_pub(con) == datakey.master_pub()
        acts = [r_[0] for r_ in con.execute("SELECT action FROM audit")]
    finally:
        con.close()
    assert any("Replaced a lost master key" in a for a in acts)
    assert "AGE-SECRET-KEY" not in " ".join(acts)
    import auth
    assert not auth.NEWKEY

def test_needs_password_and_second_factor(env):
    A, c, _ = env
    import auth
    import datakey
    r = start(c, current="wrong password here")
    assert "Your current password" in r.get_data(as_text=True) and not auth.NEWKEY
    r = start(c, code_="000000")
    assert "two-factor code" in r.get_data(as_text=True) and not auth.NEWKEY
    assert c.get("/security/new-master-key", environ_base=E).location.endswith("/security#master-key")
    assert datakey.unwrap_master(c.master_secret)

def test_a_recovery_code_stands_in_for_the_phone(env):
    A, c, _ = env
    import json
    import auth
    from werkzeug.security import generate_password_hash
    con = A.connect()
    con.execute("UPDATE users SET recovery=? WHERE id=1", (json.dumps([generate_password_hash("abcd-ef01-2345")]),))
    con.commit(); con.close()
    assert start(c, code_="abcd-ef01-2345").location.endswith("/security/new-master-key")
    assert auth.NEWKEY
    con = A.connect()
    assert json.loads(con.execute("SELECT recovery FROM users WHERE id=1").fetchone()[0]) == []
    con.close()

def test_a_wrong_ending_or_walking_away_changes_nothing(env):
    A, c, _ = env
    import auth
    import datakey
    start(c)
    new, _ = shown_key(c)
    r = c.post("/security/new-master-key", data=dict(tail="XXXXXX"), environ_base=E)
    assert new in r.get_data(as_text=True)
    assert datakey.unwrap_master(c.master_secret)
    other = A.app.test_client()
    assert "AGE-SECRET-KEY" not in other.get("/security/new-master-key", environ_base=E).get_data(as_text=True)
    for st in auth.NEWKEY.values():
        st["at"] -= auth.NEWKEY_TTL + 1
    assert c.post("/security/new-master-key", data=dict(tail=new[-6:]), environ_base=E).location.endswith("#master-key")
    assert datakey.unwrap_master(c.master_secret)
    with pytest.raises(ValueError):
        datakey.unwrap_master(new)

def test_session_keys_move_and_old_archives_are_marked_closed(env):
    A, c, tmp = env
    from vault import keys as K, seal as S, backends as B
    con = A.connect()
    try:
        K.init(con)
        K.session_pub(con, 1)
        bk = B.LocalBackend.init_root(str(tmp / "vault"))
        src = tmp / "f"; src.mkdir(); (src / "a.txt").write_text("one")
        S.seal_session(con, 1, [str(src)], [bk])
        old_wrap = con.execute("SELECT wrapped FROM vault_keys WHERE session_id=1").fetchone()[0]
    finally:
        con.close()
    start(c)
    new, _ = shown_key(c)
    c.post("/security/new-master-key", data=dict(tail=new[-6:]), environ_base=E)
    take_effect()
    con = A.connect()
    try:
        row = con.execute("SELECT wrapped, lost_wrapped FROM vault_keys WHERE session_id=1").fetchone()
        assert row[1] == old_wrap and row[0] != old_wrap
        K.session_identity(con, 1, new)
        kinds = {r_[0] for r_ in con.execute("SELECT kind FROM vault_objects")}
        assert "session" not in kinds and "session-lost" in kinds
        src2 = tmp / "g"; src2.mkdir(); (src2 / "b.txt").write_text("two")
        bk = B.LocalBackend(str(tmp / "vault"))
        S.seal_session(con, 1, [str(src2)], [bk])
        out = tmp / "out"
        S.open_session(con, 1, new, bk, str(out))
        assert [p.read_text() for p in out.rglob("b.txt")] == ["two"]
        assert not any(out.rglob("a.txt"))
    finally:
        con.close()
    acts = " ".join(r_[0] for r_ in A.connect().execute("SELECT action FROM audit"))
    assert "1 sealed session stay closed to the new key" in acts

def test_photo_keys_beside_each_photo_follow_the_new_key(env, monkeypatch):
    A, c, tmp = env
    import json
    import pyrage
    import photostore
    monkeypatch.setattr(photostore, "ROOT", tmp / "photos" / "sealed")
    from vault import keys as K
    con = A.connect()
    try:
        body = b"x" * 5000
        hid = con.execute("INSERT INTO households (name) VALUES ('Lane')").lastrowid
        sid = con.execute("INSERT INTO sessions (household_id, type) VALUES (?, 'family')", (hid,)).lastrowid
        con.commit()
        row = photostore.start(con, sid, "raw", "a.NEF", len(body), master_pub=K.master_pub(con))
        fid = row["id"]
        con.execute("UPDATE photo_files SET state='done' WHERE id=?", (fid,))
        con.commit()
    finally:
        con.close()
    start(c)
    new, _ = shown_key(c)
    c.post("/security/new-master-key", data=dict(tail=new[-6:]), environ_base=E)
    take_effect()
    m = json.loads(pyrage.decrypt(photostore.key_path(fid).read_bytes(), [K.parse_secret(new)]))
    assert m["id"] == fid
    with pytest.raises(Exception):
        pyrage.decrypt(photostore.key_path(fid).read_bytes(), [K.parse_secret(c.master_secret)])

def test_security_page_offers_it_folded(env):
    A, c, _ = env
    page = c.get("/security", environ_base=E).get_data(as_text=True)
    assert "Replace a lost master key" in page and 'name="code"' in page and "rotate the data key" in page

@pytest.fixture
def venv(tmp_path, monkeypatch):

    import io
    import json
    from datetime import date, timedelta
    store = tmp_path / "store"; store.mkdir(); (store / ".vault-root").write_text("")
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(store)}}))
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PHOTOS_ROOT", str(tmp_path / "photos"))
    monkeypatch.setenv("CRM_GALLERY_ROOT", str(tmp_path / "galleries"))
    monkeypatch.setenv("CRM_GALLERY_SYNC", "0")
    monkeypatch.delenv("CRM_UNLOCK_KEY_FILE", raising=False)
    monkeypatch.delenv("CRM_KEYRING", raising=False)
    fresh_modules()
    import app as A
    import photostore
    c = signed_in_client(A)
    shoot = (date.today() - timedelta(days=10)).isoformat()
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'shot', ?)", (shoot,))
    con.commit()
    row = photostore.start(con, 1, "raw", "a.NEF", 3000)
    photostore.append(con, row, 0, io.BytesIO(b"R" * 3000), 3000)
    con.close()
    assert c.post("/session/1/seal", data={"back": "/"}, environ_base=E).status_code == 302
    con = A.connect()
    assert {r_["state"] for r_ in photostore.files(con, 1)} == {"sealed"}
    con.close()
    return A, c, tmp_path, store

def replace(c, old="", pw=TEST_PASSWORD):

    import time
    import jobs
    r = c.post("/security", data=dict(act="newmaster", current=pw, code=code(), old_master=old), environ_base=E)
    if not r.location or not r.location.endswith("/security/new-master-key"):
        return None, r
    new, _ = shown_key(c)
    jobs.JOBS.pop("new-master", None)
    os.environ["CRM_JOBS_ASYNC"] = "1"
    try:
        r = c.post("/security/new-master-key", data=dict(tail=new[-6:]), environ_base=E)
        if not old and r.location.endswith("#master-pending"):
            take_effect(c)
    finally:
        os.environ.pop("CRM_JOBS_ASYNC", None)
    for _ in range(600):
        if jobs.JOBS.get("new-master", {}).get("state") != "running":
            break
        time.sleep(0.1)
    return new, r

def reprint(A, c, secret):
    import jobs
    jobs.JOBS.pop("unseal:1", None)
    c.post("/session/1/unseal", data={"master": secret, "back": "/"}, environ_base=E)
    return jobs.JOBS.get("unseal:1") or {}

def test_with_the_old_key_nothing_stays_closed(venv):
    A, c, tmp, store = venv
    import datakey
    import jobs
    from vault import keys as K
    from vault import seal as S, backends as B
    old = c.master_secret
    before = datakey.dk()
    con = A.connect()
    src = tmp / "f"; src.mkdir(); (src / "a.txt").write_text("one")
    S.seal_session(con, 1, [str(src)], [B.LocalBackend(str(store))])
    con.close()
    new, r = replace(c, old)
    assert r.location.endswith("/security#master-key")
    import html
    page = html.unescape(c.get("/security", environ_base=E).get_data(as_text=True))
    assert "sealed sessions and photos move to it" in page and "rotate the data key" in page
    assert datakey.unwrap_master(new) == before
    with pytest.raises(ValueError):
        datakey.unwrap_master(old)
    con = A.connect()
    try:
        K.session_identity(con, 1, new)
        S.open_session(con, 1, new, B.LocalBackend(str(store)), str(tmp / "out"))
        assert [p.read_text() for p in (tmp / "out").rglob("a.txt")] == ["one"]
        kinds = {r_[0] for r_ in con.execute("SELECT kind FROM vault_objects")}
        acts = " ".join(r_[0] for r_ in con.execute("SELECT action FROM audit"))
    finally:
        con.close()
    assert not any(k.endswith("-lost") for k in kinds)
    assert "the old one given" in acts and "AGE-SECRET-KEY" not in acts
    assert jobs.JOBS["new-master"]["state"] == "done", jobs.JOBS["new-master"]
    assert "2 key files in the vault moved to the new key" in jobs.JOBS["new-master"]["message"]
    j = reprint(A, c, new)
    assert j.get("state") == "done", j
    from pathlib import Path
    con = A.connect()
    d = con.execute("SELECT unsealed_dir FROM retention WHERE session_id=1").fetchone()[0]
    con.close()
    assert (Path(d) / "raw" / "a.NEF").read_bytes() == b"R" * 3000
    import auth
    assert not auth.NEWKEY

def test_a_wrong_old_key_changes_nothing(env):
    A, c, _ = env
    import json
    import auth
    import datakey
    from vault import keys as K
    from werkzeug.security import generate_password_hash
    con = A.connect()
    con.execute("UPDATE users SET recovery=? WHERE id=1", (json.dumps([generate_password_hash("abcd-ef01-2345")]),))
    con.commit(); con.close()
    other, _ = K.generate_master()
    for wrong in (other, "not a key at all"):
        r = c.post("/security", data=dict(act="newmaster", current=TEST_PASSWORD, code="abcd-ef01-2345", old_master=wrong),
                   environ_base=E)
        import html
        assert "isn't the master key in use now, so nothing was changed" in html.unescape(r.get_data(as_text=True))
        assert not auth.NEWKEY
    assert datakey.unwrap_master(c.master_secret)
    con = A.connect()
    assert len(json.loads(con.execute("SELECT recovery FROM users WHERE id=1").fetchone()[0])) == 1
    con.close()

def test_a_session_sealed_with_the_lost_key_says_so_plainly(venv):
    A, c, tmp, store = venv
    from vault import keys as K
    new, _ = replace(c)
    import html
    page = html.unescape(c.get("/c/1?s=1&show=storage", environ_base=E).get_data(as_text=True))
    assert K.LOST_TEXT in page and "Open for a reprint" not in page
    j = reprint(A, c, new)
    assert j.get("state") == "error" and j.get("message") == K.LOST_TEXT

def test_photo_copies_on_the_backup_location_follow_the_new_key(venv):
    A, c, tmp, store = venv
    import io
    import json
    import pyrage
    import photostore
    from vault import backends as B, keys as K
    con = A.connect()
    row = photostore.start(con, 1, "export", "b.jpg", 2000, master_pub=K.master_pub(con))
    photostore.append(con, row, 0, io.BytesIO(b"J" * 2000), 2000)
    bk = B.LocalBackend(str(store))
    photostore.mirror_pass(con, bk)
    con.close()
    kname = photostore.MIRROR + f"{row['id']}.key"
    new, _ = replace(c)
    import jobs
    assert "1 copied to the backup location" in jobs.JOBS["new-master"]["message"], jobs.JOBS["new-master"]
    buf = io.BytesIO()
    bk.get_stream(kname, buf)
    assert json.loads(pyrage.decrypt(buf.getvalue(), [K.parse_secret(new)]))["id"] == row["id"]
    with pytest.raises(Exception):
        pyrage.decrypt(buf.getvalue(), [K.parse_secret(c.master_secret)])

def test_the_sign_in_change_notice(env, monkeypatch):
    A, c, _ = env
    import json
    from datetime import datetime, timedelta
    import inboxnotice as N
    sent = []
    monkeypatch.setattr(N.locknotice, "_post", lambda url, body, headers: sent.append((body.decode(), dict(headers))))
    con = A.connect()
    for k, v in (("ntfy_server", "https://ntfy.example.net"), ("ntfy_topic", "studio-k3j9x2q7"), ("ntfy_token", "")):
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (k, v))
    con.commit(); con.close()
    with A.app.app_context():
        cfg = A.ntfy_settings()

    def pending():
        con_ = A.connect()
        try:
            return N._get(con_, N.KEY_CRED_AT)
        finally:
            con_.close()
    page = c.get("/settings?tab=notifications", environ_base=E).get_data(as_text=True)
    assert 'data-autosubmit checked>Your password, master key, or recovery codes changed' in page
    c.post("/security/notice", data=dict(act="cred"), environ_base=E)
    page = c.get("/settings?tab=notifications", environ_base=E).get_data(as_text=True)
    assert 'name="cred_on" value="1" data-autosubmit>' in page
    c.post("/security", data=dict(act="codes", current=TEST_PASSWORD), environ_base=E)
    assert not pending() and N.cred_tick(A.connect, cfg) is None and not sent
    c.post("/security/notice", data=dict(act="cred", cred_on="1"), environ_base=E)
    assert 'data-autosubmit checked>Your password, master key, or recovery codes changed' in \
        c.get("/settings?tab=notifications", environ_base=E).get_data(as_text=True)
    for act in (dict(act="codes"), dict(act="password", password="another long pass 1", password2="another long pass 1")):
        c.post("/security", data=dict(current=TEST_PASSWORD, **act), environ_base=E)
        assert pending()
        assert N.cred_tick(A.connect, cfg) == "sent"
        assert N.cred_tick(A.connect, cfg) is None
    assert len(sent) == 2
    body, headers = sent[0]
    assert headers["Title"] == "Studio sign-in changed" == N.CRED_TITLE
    assert body == N.CRED_TEXT and "password" not in body.lower() and "recovery" not in body.lower()
    monkeypatch.setattr(N.locknotice, "_post", lambda *a: (_ for _ in ()).throw(OSError("down")))
    assert replace(c, pw="another long pass 1")[0]
    t0 = datetime.now()
    assert N.cred_tick(A.connect, cfg, t0) == "failed"
    assert N.cred_tick(A.connect, cfg, t0 + timedelta(minutes=2)) is None
    monkeypatch.setattr(N.locknotice, "_post", lambda url, body, headers: sent.append((body, headers)))
    assert N.cred_tick(A.connect, cfg, t0 + N.RETRY) == "sent"
    acts = " ".join(r_[0] for r_ in A.connect().execute("SELECT action FROM audit"))
    assert "Turned on the sign-in change notice" in acts
