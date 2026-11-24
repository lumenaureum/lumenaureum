import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import TEST_PASSWORD, fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

@pytest.fixture
def studio(tmp_path, monkeypatch):

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
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Lane')")
    con.execute("INSERT INTO sessions (household_id, type, stage) VALUES (1, 'family', 'shot')")
    con.commit()
    from vault import keys as K
    row = photostore.start(con, 1, "raw", "a.NEF", 3000, master_pub=K.master_pub(con))
    photostore.append(con, row, 0, io.BytesIO(b"R" * 3000), 3000)
    con.close()
    import jobs
    jobs.JOBS.clear()
    yield A, c, tmp_path, store
    jobs.JOBS.clear()

def _opens_with(blob, secret):
    import pyrage
    from vault import keys as K
    try:
        return json.loads(pyrage.decrypt(blob, [K.parse_secret(secret)]))
    except Exception:
        return None

def _replace_lost_without_job(A):

    from vault import keys as K, web as VW
    secret, pub = K.generate_master()
    VW.replace_lost(A.connect, pub)
    return secret

def test_a_seal_after_a_replacement_seals_the_key_to_the_new_master(studio):
    A, c, tmp, store = studio
    import jobs
    import photostore
    old = c.master_secret
    con = A.connect()
    fid = con.execute("SELECT id FROM photo_files").fetchone()[0]
    con.close()
    assert _opens_with(photostore.key_path(fid).read_bytes(), old)
    new = _replace_lost_without_job(A)
    c.post("/session/1/seal", data={"back": "/"}, environ_base=E)
    con = A.connect()
    r = con.execute("SELECT state, fkey FROM photo_files").fetchone()
    kinds = {r_[0] for r_ in con.execute("SELECT kind FROM vault_objects")}
    con.close()
    assert r["state"] == "sealed" and r["fkey"] == ""
    assert "photo-key" in kinds
    kb = next(store.rglob(f"{fid}.key")).read_bytes()
    assert _opens_with(kb, new)["id"] == fid and _opens_with(kb, old) is None
    jobs.JOBS.pop("unseal:1", None)
    c.post("/session/1/unseal", data={"master": new, "back": "/"}, environ_base=E)
    assert jobs.JOBS["unseal:1"]["state"] == "done", jobs.JOBS["unseal:1"]

def test_a_seal_waits_while_photo_keys_move(studio):
    A, c, tmp, store = studio
    import time
    import jobs
    from vault import web as VW
    for key in ("new-master", f"new-master:{time.time_ns()}"):
        jobs.JOBS.clear()
        jobs.JOBS[key] = dict(key=key, label="", state="running", done=0, total=0, message="", started=time.time(),
                              ended=None, link="")
        assert VW.new_master_running()
        c.post("/session/1/seal", data={"back": "/"}, environ_base=E)
        assert "Not sealed yet" in jobs.JOBS["seal:1"]["message"]
        con = A.connect()
        r = con.execute("SELECT state, fkey FROM photo_files").fetchone()
        sealed = con.execute("SELECT sealed_at FROM retention WHERE session_id=1").fetchone()
        con.close()
        assert r["state"] == "done" and r["fkey"] and not (sealed and sealed[0])
    jobs.JOBS.clear()
    assert not VW.new_master_running()

def test_a_master_key_changed_during_the_copy_leaves_the_photo_as_it_was(studio):
    A, c, tmp, store = studio
    import photostore
    from vault import backends as B, keys as K
    bk = B.LocalBackend(str(store))

    class Swapping:
        def __init__(self):
            self.done = False

        def put_stream(self, name, f):
            bk.put_stream(name, f)
            if not self.done:
                self.done = True
                c2 = A.connect()
                K.set_master_pub(c2, K.generate_master()[1])
                c2.close()

        def __getattr__(self, k):
            return getattr(bk, k)
    con = A.connect()
    r = con.execute("SELECT * FROM photo_files").fetchone()
    with pytest.raises(IOError, match="master key changed"):
        photostore.seal_to_vault(con, [r], [Swapping()], K.master_pub(con))
    r = con.execute("SELECT state, fkey FROM photo_files").fetchone()
    con.close()
    assert r["state"] == "done" and r["fkey"]

def test_one_bad_photo_does_not_stop_the_reseal(studio, monkeypatch):
    A, c, tmp, store = studio
    import photostore
    con = A.connect()
    row = photostore.start(con, 1, "raw", "b.NEF", 2000)
    photostore.append(con, row, 0, io.BytesIO(b"S" * 2000), 2000)
    con.close()
    new = _replace_lost_without_job(A)
    real = photostore.write_key

    def flaky(r, pub, label="", path=None):
        if r["name"] == "a.NEF":
            raise OSError("disk error")
        return real(r, pub, label, path)
    monkeypatch.setattr(photostore, "write_key", flaky)
    import jobs
    from vault import web as VW
    VW.after_new_master(A.app, A.connect)
    j = jobs.JOBS["new-master"]
    assert j["state"] == "done", j
    assert "1 photo key sealed" in j["message"] and "1 could not be (a.NEF" in j["message"]
    con = A.connect()
    fid_b = con.execute("SELECT id FROM photo_files WHERE name='b.NEF'").fetchone()[0]
    con.close()
    assert _opens_with(photostore.key_path(fid_b).read_bytes(), new)["id"] == fid_b

def test_a_second_replacement_is_not_dropped_and_a_changed_key_runs_again(studio, monkeypatch):
    A, c, tmp, store = studio
    import time
    import jobs
    import photostore
    from vault import keys as K, web as VW
    con = A.connect()
    fid = con.execute("SELECT id FROM photo_files").fetchone()[0]
    con.close()
    jobs.JOBS["new-master"] = dict(key="new-master", label="", state="running", done=0, total=0, message="",
                                   started=time.time(), ended=None, link="")
    first = _replace_lost_without_job(A)
    VW.after_new_master(A.app, A.connect)
    later = [k for k in jobs.JOBS if k.startswith("new-master:")]
    assert later and jobs.JOBS[later[0]]["state"] == "done", jobs.JOBS
    assert _opens_with(photostore.key_path(fid).read_bytes(), first)
    jobs.JOBS.clear()
    second, second_pub = K.generate_master()
    real, calls = photostore.reseal_keys, []

    def reseal(con, pub, errors=None):
        calls.append(pub)
        n = real(con, pub, errors)
        if len(calls) == 1:
            K.set_master_pub(con, second_pub)
        return n
    monkeypatch.setattr(photostore, "reseal_keys", reseal)
    _replace_lost_without_job(A)
    VW.after_new_master(A.app, A.connect)
    assert len(calls) == 2 and calls[1] == second_pub
    assert _opens_with(photostore.key_path(fid).read_bytes(), second)

def test_a_replacement_in_progress_leaves_memory_at_lock_and_after_ten_minutes(studio):
    A, c, tmp, store = studio
    import auth
    import datakey
    r = c.post("/security", data=dict(act="newmaster", current=TEST_PASSWORD, code=auth.totp("JBSWY3DPEHPK3PXP"),
                                      old_master=c.master_secret), environ_base=E)
    assert r.location.endswith("/security/new-master-key") and auth.NEWKEY
    assert any(st.get("old") for st in auth.NEWKEY.values())
    assert auth.NEWKEY.clear in datakey.ON_LOCK
    datakey.lock()
    assert not auth.NEWKEY
    auth.NEWKEY["t"] = dict(secret="x", pub="y", tail="X", at=0, uid=1, sid="s", old="z")
    auth.NEWKEY["u"] = dict(secret="x", pub="y", tail="X", at=__import__("time").time(), uid=1, sid="s", old="")
    auth._newkey_purge()
    assert list(auth.NEWKEY) == ["u"]
    auth.NEWKEY["t"] = dict(secret="x", pub="y", tail="X", at=0, uid=1, sid="s", old="z")
    with A.app.test_request_context("/security", environ_base=E):
        try:
            A.app.view_functions["security"]()
        except Exception:
            pass
    assert "t" not in auth.NEWKEY
    auth.NEWKEY.clear()
