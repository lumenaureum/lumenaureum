import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _install(tmp):
    data, vault = tmp / "data", tmp / "vault"
    data.mkdir(parents=True, exist_ok=True)
    vault.mkdir(parents=True, exist_ok=True)
    (vault / ".vault-root").touch()
    (data / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(vault)}}))
    return data

def run(code, data):
    env = dict(os.environ, CRM_TESTING="1", CRM_DB=str(data / "crm.db"), CRM_STORAGE_CFG=str(data / "storage.json"),
               CRM_PHOTOS_ROOT=str(data.parent / "photos"), CRM_GALLERY_ROOT=str(data / "galleries"),
               CRM_GALLERY_SYNC="0")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-4000:]
    return r.stdout

SETUP = """
import sys, re, os, io; sys.path.insert(0, 'tests')
from helpers import signed_in_client, TEST_PASSWORD
import app as A, datakey, restore as R
from vault import backup as BK
c = signed_in_client(A)
secret = c.master_secret
ip = {'environ_base': {'REMOTE_ADDR': '127.0.0.1'}}
def sql(q, a=()):
    con = A.connect(); r = con.execute(q, a).fetchall(); con.commit(); con.close(); return [tuple(x) for x in r]
def text(r):
    return ' '.join(re.sub(r'<[^>]+>', ' ', r.get_data(as_text=True)).split())
"""

def test_stale_check_on_another_device_wipes_the_studio(tmp_path):

    data = _install(tmp_path)
    out = run(SETUP + """
sql("INSERT INTO households (name) VALUES ('Client A')")
name = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]
sql("INSERT INTO households (name) VALUES ('Client B')")
phone = A.app.test_client()                      # the same owner, signed in on a second device
with c.session_transaction() as s: shared = dict(s)
with phone.session_transaction() as s: s.update(shared)
print('phone check', phone.post('/vault/restore', data={'go': 'check', 'source': name, 'secret': secret}, **ip).status_code)
print('laptop check', c.post('/vault/restore', data={'go': 'check', 'source': name, 'secret': secret}, **ip).status_code)
phone.get('/vault/restore?step=review', **ip)    # the phone's tab is reloaded
r = c.post('/vault/restore', data={'go': 'restore', 'password': TEST_PASSWORD}, **ip)
print('laptop restore', r.status_code)
print('keyring', datakey.exists())
print('data', sorted(f for f in os.listdir(A.DB_PATH.parent) if not f.startswith('.')))
print('home', A.app.test_client().get('/', **ip).headers.get('Location'))
if datakey.exists():
    datakey.set_unlocked(datakey.unwrap_password(TEST_PASSWORD)); A.on_unlock()
    print('clients', [x[0] for x in sql("SELECT name FROM households ORDER BY id")])
""", data)
    assert "phone check 302" in out and "laptop check 302" in out, out
    assert "keyring True" in out, out
    assert "/setup/owner" not in out, out
    assert "clients ['Client A']" in out or "clients ['Client A', 'Client B']" in out, out

def test_restore_button_restores_a_different_backup_than_the_one_reviewed(tmp_path):

    data = _install(tmp_path)
    out = run(SETUP + """
sql("INSERT INTO households (name) VALUES ('Client A')")
older = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]
sql("INSERT INTO households (name) VALUES ('Client B')")
newer = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]
sql("INSERT INTO households (name) VALUES ('Client C')")
c.post('/vault/restore', data={'go': 'check', 'source': newer, 'secret': secret}, **ip)
rv = c.get('/vault/restore?step=review', **ip)
t = text(rv); i = t.index('What changes')
print('tab1 shows', 'Clients 3 2' in t[i:])
form = rv.get_data(as_text=True); form = form[form.index('id="restore-confirm"'):]
fields = {k: v for k, v in re.findall(r'name="([^"]+)"(?: value="([^"]*)")?', form[:form.index('</form>')])}
c.post('/vault/restore', data={'go': 'check', 'source': older, 'secret': secret}, **ip)   # tab 2
fields['password'] = TEST_PASSWORD
r = c.post('/vault/restore', data=fields, **ip)                                           # tab 1's button
print('restore', r.status_code)
if not datakey.is_unlocked():
    datakey.set_unlocked(datakey.unwrap_password(TEST_PASSWORD)); A.on_unlock()
print('clients', [x[0] for x in sql("SELECT name FROM households ORDER BY id")])
""", data)
    assert "tab1 shows True" in out, out

    assert "clients ['Client A', 'Client B']" in out or "clients ['Client A', 'Client B', 'Client C']" in out, out

def test_before_a_reset_copy_is_not_offered_and_loses_face_photos(tmp_path):

    data = _install(tmp_path)
    out = run(SETUP + """
sql("INSERT INTO households (name) VALUES ('Client A')")
A.FACES.mkdir(parents=True, exist_ok=True)
datakey.write_sealed(A.FACES / '1.jpg', b'\\xff\\xd8 a face', 'face:1', label='faces')
print('reset', c.post('/settings/reset', data={'confirm': 'RESET'}, **ip).status_code)
print('after reset', sql("SELECT COUNT(*) FROM households")[0][0], A.FACES.exists())
copy = sql("SELECT name FROM vault_objects WHERE kind='reset' ORDER BY id")[0][0]
page = c.get('/vault/restore', **ip).get_data(as_text=True)
print('offered', copy in page)
r = c.post('/vault/restore', data={'go': 'check', 'source': copy, 'secret': secret}, **ip)
t = text(c.get('/vault/restore?step=review', **ip))
print('faces line', 'The face photos come back from the backup too.' in t, 'No face photos file was made' in t)
r = c.post('/vault/restore', data={'go': 'restore', 'password': TEST_PASSWORD}, **ip)
datakey.set_unlocked(datakey.unwrap_password(TEST_PASSWORD)); A.on_unlock()
print('clients', [x[0] for x in sql("SELECT name FROM households")])
print('faces', sorted(os.listdir(A.FACES)) if A.FACES.exists() else [])
""", data)
    assert "after reset 0 False" in out, out
    assert "clients ['Client A']" in out, out
    assert "offered True" in out, out
    assert "faces line True False" in out, out
    assert "faces ['1.jpg']" in out, out

def test_a_pre_reset_copy_from_before_1_6_1_finds_its_faces_file():

    import sqlite3
    sys.path.insert(0, ROOT)
    from vault import web as W
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE vault_objects (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, created TEXT, deleted TEXT)")
    for kind, name, at in (("reset", "db/a.age", "2026-11-01T10:00:00"), ("reset", "db/af.age", "2026-11-01T10:00:05"),
                           ("reset", "db/b.age", "2026-11-01T11:00:00"),
                           ("reset", "db/c.age", "2026-11-02T10:00:00"), ("reset-faces", "db/cf.age", "2026-11-02T10:00:04"),
                           ("reset", "db/d.age", "2026-11-02T10:01:00")):
        con.execute("INSERT INTO vault_objects (kind, name, created) VALUES (?,?,?)", (kind, name, at))
    assert W.reset_faces_before_161(con) == {"db/a.age": "db/af.age"}
    import restore as R
    assert [R.faces_partner(con, n) for n in ("db/a.age", "db/b.age", "db/c.age", "db/d.age")] == \
        ["db/af.age", None, "db/cf.age", None]
