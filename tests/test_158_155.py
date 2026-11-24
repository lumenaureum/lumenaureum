import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "templates", "restore.html")

def _install(tmp, vault):
    data = tmp / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.makedirs(vault, exist_ok=True)
    open(os.path.join(vault, ".vault-root"), "a").close()
    (data / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(vault)}}))
    return data

def run(code, data, **extra):
    env = dict(os.environ, CRM_DB=str(data / "crm.db"), CRM_STORAGE_CFG=str(data / "storage.json"),
               CRM_PHOTOS_ROOT=str(data.parent / "photos"), CRM_GALLERY_ROOT=str(data / "galleries"),
               CRM_GALLERY_SYNC="0", **extra)
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-4000:]
    return r.stdout

SETUP = """
import sys, re, os; sys.path.insert(0, 'tests')
from helpers import signed_in_client, TEST_PASSWORD
import app as A, datakey, restore as R
from vault import backup as BK
c = signed_in_client(A)
secret = c.master_secret
def ip(): return {'environ_base': {'REMOTE_ADDR': '127.0.0.1'}}
def sql(q, a=()):
    con = A.connect(); r = con.execute(q, a).fetchall(); con.commit(); con.close(); return r
def gallery(sid, gid, state='up'):
    sql("UPDATE sessions SET gallery_id=? WHERE id=?", (gid, sid))
    sql("INSERT INTO galleries (session_id, key, upload_state, host_fp) VALUES (?, 'k', ?, 'fp1')", (sid, state))
def signin():
    datakey.set_unlocked(datakey.unwrap_password(TEST_PASSWORD)); A.on_unlock()
"""

def test_restore_in_the_app_round_trip_and_back(tmp_path):
    data = _install(tmp_path, str(tmp_path / "vault"))
    out = run(SETUP + """
for n in ('Client A', 'Client B'):
    sql("INSERT INTO households (name) VALUES (?)", (n,))
sql("INSERT INTO sessions (household_id, type) VALUES (1, 'senior')")
sql("INSERT INTO sessions (household_id, type) VALUES (2, 'family')")
gallery(1, 'galleryone1')                                   # online in the backup, taken down after it
A.FACES.mkdir(parents=True, exist_ok=True); (A.FACES / '1.jpg').write_bytes(b'face')
name = [n for n in BK.sealed_backup(str(A.DB_PATH), str(A.FACES), kind='manual') if True][0]
# changes after the backup
sql("INSERT INTO households (name) VALUES ('Client C')")
sql("INSERT INTO sessions (household_id, type) VALUES (3, 'senior')")
sql("UPDATE galleries SET upload_state='removed', removed_at='2026-11-20' WHERE session_id=1")
(A.FACES / '2.jpg').write_bytes(b'face two')                # a face added since: set aside, not in the backup
gallery(3, 'gallerythree')                                  # online now, not in the backup
newer = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]
r = c.get('/vault'); print('vault link', '/vault/restore' in r.get_data(as_text=True))
page = c.get('/vault/restore').get_data(as_text=True)
print('listed', name in page and newer in page)
r = c.post('/vault/restore', data={'go': 'check', 'source': name, 'secret': 'AGE-SECRET-KEY-1' + 'Q' * 58}, **ip())
print('wrong key', r.status_code, 'open this backup' in r.get_data(as_text=True))
r = c.post('/vault/restore', data={'go': 'check', 'source': name, 'secret': secret}, **ip())
print('check', r.status_code, r.headers.get('Location'))
rv = c.get('/vault/restore?step=review').get_data(as_text=True)
print('checked', 'restore-checked' in rv, 'restore-unchecked' in rv)
t = re.sub(r'<[^>]+>', ' ', rv[rv.index('restore-changes'):]); t = ' '.join(t.split())
print('counts', 'Clients 3 2' in t, 'Sessions 3 2' in t, 'Galleries online 1 1' in t)
print('notes', '1 gallery online now is not online in the backup' in t, '1 gallery the backup has online was taken down since' in t)
print('scratch', sorted(os.listdir(R.stage_dir())))
print('faces note', 'The face photos come back from the backup too.' in t)
r = c.post('/vault/restore', data={'go': 'restore', 'password': 'not my password at all'}, **ip())
print('bad pw', r.status_code, 'Nothing was changed' in r.get_data(as_text=True), sql("SELECT COUNT(*) FROM households")[0][0])
r = c.post('/vault/restore', data={'go': 'restore', 'password': TEST_PASSWORD}, **ip())
d = r.get_data(as_text=True)
print('done', r.status_code, 'Sign in to the restored studio' in d, 'Sign in with your password, as before.' in d)
print('locked', not datakey.is_unlocked(), c.get('/').status_code, '/login' in c.get('/').headers.get('Location', ''))
aside = R.set_aside_copies()
print('aside', len(aside), sorted(os.listdir(aside[0]['path'])))
print('scratch gone', not R.stage_dir().exists())
signin()
print('after', [r_[0] for r_ in sql("SELECT name FROM households ORDER BY id")])
print('leftovers', [tuple(r_) for r_ in sql("SELECT gallery_id, host_fp FROM gallery_leftovers")])
print('g1', [tuple(r_) for r_ in sql("SELECT upload_state, removed_at IS NOT NULL FROM galleries WHERE session_id=1")])
print('newer kept', bool(sql("SELECT 1 FROM vault_objects WHERE name=? AND sha256 IS NOT NULL", (newer,))))
print('history', bool(sql("SELECT 1 FROM audit WHERE action LIKE 'Restored the backup of %set-aside/%'")))
print('faces', sorted(os.listdir(A.FACES)), sorted(os.listdir(os.path.join(aside[0]['path'], 'faces'))))
print('readme 30', '30 days' in open(os.path.join(aside[0]['path'], 'README.txt')).read())
# going back: the data set aside is one of the choices
c = signed_in_client(A)
page = c.get('/vault/restore').get_data(as_text=True)
print('aside listed', 'aside:' + aside[0]['stamp'] in page)
r = c.post('/vault/restore', data={'go': 'check', 'source': 'aside:' + aside[0]['stamp'], 'secret': secret}, **ip())
print('aside check', r.status_code)
r = c.post('/vault/restore', data={'go': 'restore', 'password': TEST_PASSWORD}, **ip())
print('aside done', r.status_code)
signin()
print('back', [r_[0] for r_ in sql("SELECT name FROM households ORDER BY id")])
print('two asides', len(R.set_aside_copies()))
print('faces back', sorted(os.listdir(A.FACES)))
""", data)
    assert "vault link True" in out and "listed True" in out, out
    assert "wrong key 400 True" in out, out
    assert "check 302 /vault/restore?step=review" in out, out
    assert "checked True False" in out, out
    assert "counts True True True" in out and "notes True True" in out, out
    assert "scratch ['crm.db', 'faces', 'keyring.json', 'storage.json']" in out, out
    assert "faces note True" in out, out
    assert "bad pw 400 True 3" in out, out
    assert "done 200 True True" in out and "locked True 302 True" in out, out
    assert "aside 1 ['README.txt', 'crm.db', 'deletions.json', 'doc-numbers.json', 'faces', 'keyring.json', 'storage.json']" in out \
        or "aside 1 ['README.txt', 'crm.db', 'faces', 'keyring.json', 'storage.json']" in out, out
    assert "scratch gone True" in out, out
    assert "after ['Client A', 'Client B']" in out, out
    assert "leftovers [('gallerythree', 'fp1')]" in out, out
    assert "g1 [('removed', 1)]" in out, out
    assert "newer kept True" in out and "history True" in out, out
    assert "faces ['1.jpg'] ['1.jpg', '2.jpg']" in out and "readme 30 True" in out, out
    assert "aside listed True" in out and "aside check 302" in out and "aside done 200" in out, out
    assert "back ['Client A', 'Client B', 'Client C']" in out and "two asides 2" in out, out
    assert "faces back ['1.jpg', '2.jpg']" in out, out

def test_uploaded_file_is_restored_only_after_the_unchecked_warning(tmp_path):
    data = _install(tmp_path, str(tmp_path / "vault"))
    out = run(SETUP + """
import io
sql("INSERT INTO households (name) VALUES ('Client A')")
name = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]
blob = open(os.path.join(str(tmp_path_vault), name), 'rb').read()
r = c.post('/vault/restore', data={'go': 'check', 'secret': secret, 'file': (io.BytesIO(blob), 'backup.age')},
           content_type='multipart/form-data', **ip())
print('check', r.status_code)
rv = c.get('/vault/restore?step=review').get_data(as_text=True)
print('unchecked', 'restore-unchecked' in rv, 'Not checked:' in rv)
print('upload faces', 'The face photos here stay as they are.' in rv)
print('upload gone', not R.upload_dir().exists())
r = c.post('/vault/restore', data={'go': 'check', 'secret': secret, 'file': (io.BytesIO(b'not a backup'), 'x.age')},
           content_type='multipart/form-data', **ip())
print('junk', r.status_code, 'restore-err' in r.get_data(as_text=True))
print('no scratch', not R.stage_dir().exists())
try:
    R.check(name, secret, connect=A.connect, schema_now=0, fresh=False)
except R.Refused as e:
    print('newer', 'newer version' in str(e))
""".replace("tmp_path_vault", repr(str(tmp_path / "vault"))), data)
    assert "check 302" in out and "unchecked True True" in out and "upload gone True" in out, out
    assert "upload faces True" in out, out
    assert "junk 400 True" in out and "no scratch True" in out and "newer True" in out, out

def test_new_install_first_screen_restores(tmp_path):
    vault = str(tmp_path / "vault")
    old = _install(tmp_path / "old", vault)
    out = run(SETUP + """
sql("INSERT INTO households (name) VALUES ('Client A')")
sql("INSERT INTO sessions (household_id, type) VALUES (1, 'senior')")
A.FACES.mkdir(parents=True, exist_ok=True); (A.FACES / '7.jpg').write_bytes(b'face')
BK.sealed_backup(str(A.DB_PATH), str(A.FACES), kind='manual')
print('SECRET', secret)
""", old)
    secret = out.split("SECRET ")[1].split()[0]
    new = tmp_path / "new" / "data"
    new.mkdir(parents=True)
    out = run("""
import sys, os, re; sys.path.insert(0, 'tests')
from helpers import TEST_PASSWORD
import app as A, datakey, netsec, restore as R
from helpers import HttpsClient
A.app.test_client_class = HttpsClient
c = A.app.test_client()
def ip(): return {'environ_base': {'REMOTE_ADDR': '127.0.0.1'}}
r = c.get('/'); print('first', r.headers.get('Location'))
p = c.get('/setup/owner').get_data(as_text=True); print('offered', '/setup/restore' in p)
p = c.get('/setup/restore?folder=' + VAULT).get_data(as_text=True)
print('before the code', len(re.findall(r'value="folder:(db/[^"]+)"', p)))
p = c.post('/setup/restore', data={'go': 'list', 'folder': VAULT, 'claim': netsec.claim_code()}, **ip()).get_data(as_text=True)
names = re.findall(r'value="folder:(db/[^"]+)"', p); print('found', len(names))
src = 'folder:' + names[0]
r = c.post('/setup/restore', data={'go': 'check', 'source': src, 'folder': VAULT, 'secret': SECRET, 'claim': 'WRONG'}, **ip())
print('claim', r.status_code, 'Wrong setup code' in r.get_data(as_text=True))
def check(n):
    return c.post('/setup/restore', data={'go': 'check', 'source': 'folder:' + n, 'folder': VAULT, 'secret': SECRET,
                                          'claim': netsec.claim_code()}, **ip())
ok = []
for n in names:                                     # both files are listed; which one shows first is not the point
    r = check(n)
    if r.status_code == 302:
        ok.append(n)
    else:
        print('skipped', 'face photos' in r.get_data(as_text=True))
print('one backup', len(ok))
check(ok[0])                                        # a refused check forgets the one before it: check the backup last
rv = c.get('/setup/restore?step=review').get_data(as_text=True)
t = ' '.join(re.sub(r'<[^>]+>', ' ', rv).split())
print('review', 'restore-unchecked' in rv, 'Clients 1' in t, 'Now' not in t.split('What changes')[1][:40])
r = c.post('/setup/restore', data={'go': 'restore'}, **ip())
print('done', r.status_code, 'Sign in to the restored studio' in r.get_data(as_text=True))
print('owned', datakey.exists(), c.get('/setup/restore').status_code, c.get('/').headers.get('Location'))
print('no aside', not R.aside_root().exists(), 'claim gone', not netsec.code_file().exists())
datakey.set_unlocked(datakey.unwrap_password(TEST_PASSWORD)); A.on_unlock()
con = A.connect(); print('restored', [r_[0] for r_ in con.execute('SELECT name FROM households')]); con.close()
print('new faces', sorted(os.listdir(A.FACES)))
""".replace("VAULT", repr(vault)).replace("SECRET", repr(secret)), new)
    assert "first /setup/owner" in out and "offered True" in out, out
    assert "before the code 0" in out
    assert "found 2" in out and out.count("skipped True") == 1 and "one backup 1" in out, out
    assert "claim 400 True" in out, out
    assert "review True True True" in out, out
    assert "done 200 True" in out and "owned True 404 /login" in out, out
    assert "no aside True" in out and "claim gone True" in out, out
    assert "restored ['Client A']" in out and "new faces ['7.jpg']" in out, out

def test_new_install_finds_the_faces_file_stamped_the_same_time(tmp_path):

    vault = str(tmp_path / "vault")
    old = _install(tmp_path / "old", vault)
    out = run(SETUP + """
sql("INSERT INTO households (name) VALUES ('Client A')")
A.FACES.mkdir(parents=True, exist_ok=True); (A.FACES / '7.jpg').write_bytes(b'face')
print('NAMES', *BK.sealed_backup(str(A.DB_PATH), str(A.FACES), kind='manual'))
print('SECRET', secret)
""", old)
    secret = out.split("SECRET ")[1].split()[0]
    db_, faces_ = out.split("NAMES ")[1].split()[:2]
    d = os.path.join(vault, "db")
    bk, fc = "db/" + "f" * 32 + ".age", "db/" + "0" * 32 + ".age"
    os.rename(os.path.join(vault, db_), os.path.join(vault, bk))
    os.rename(os.path.join(vault, faces_), os.path.join(vault, fc))
    t = os.stat(os.path.join(vault, bk)).st_mtime_ns
    for n in (bk, fc):
        os.utime(os.path.join(vault, n), ns=(t, t))
    later = os.path.join(d, "1" * 32 + ".age")
    open(later, "wb").write(b"not a backup")
    os.utime(later, ns=(t + 60 * 10**9,) * 2)
    new = tmp_path / "new" / "data"
    new.mkdir(parents=True)
    out = run("""
import sys, os; sys.path.insert(0, 'tests')
from helpers import TEST_PASSWORD
import app as A, datakey, netsec, restore as R
from helpers import HttpsClient
A.app.test_client_class = HttpsClient
c = A.app.test_client()
print('listed', [r['name'] for r in R.folder_backups(VAULT)])
print('partners', R.folder_partners(VAULT, BK))
r = c.post('/setup/restore', data={'go': 'check', 'source': 'folder:' + BK, 'folder': VAULT, 'secret': SECRET,
                                   'claim': netsec.claim_code()}, environ_base={'REMOTE_ADDR': '127.0.0.1'})
print('checked', r.status_code)
r = c.post('/setup/restore', data={'go': 'restore'}, environ_base={'REMOTE_ADDR': '127.0.0.1'})
print('done', r.status_code)
print('new faces', sorted(os.listdir(A.FACES)))
""".replace("VAULT", repr(vault)).replace("SECRET", repr(secret)).replace("BK", repr(bk)), new)
    assert f"listed ['db/{'1' * 32}.age', '{bk}', '{fc}']" in out, out
    assert f"partners ['{fc}', 'db/{'1' * 32}.age']" in out, out
    assert "checked 302" in out and "done 200" in out and "new faces ['7.jpg']" in out, out

def test_screens_use_the_shared_library():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_ui_library import bare_labels, one_offs
    text = open(TEMPLATE).read()
    assert not one_offs(text) and not bare_labels(text)
    for words in ("Check this backup", "Restore and restart", "Sign in to the restored studio"):
        assert text.count(words) == 1

def test_waiting_master_key_refuses_and_backup_wait_is_dropped(tmp_path):

    data = _install(tmp_path, str(tmp_path / "vault"))
    out = run(SETUP + """
import json, datetime as dt
from vault import keys as K
sql("INSERT INTO households (name) VALUES ('Client A')")
def wait():
    due = (dt.datetime.now() + dt.timedelta(hours=40)).isoformat(timespec='seconds')
    sql("INSERT INTO settings (key, value) VALUES ('master_pending', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps({'pub': K.master_pub(A.connect()), 'asked': dt.datetime.now().isoformat(), 'due': due}),))
wait()
name = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]          # made during the wait
r = c.post('/vault/restore', data={'go': 'check', 'source': name, 'secret': secret}, **ip())
d = r.get_data(as_text=True)
print('refused', r.status_code, 'waiting to take effect' in d, not R.stage_dir().exists())
sql("DELETE FROM settings WHERE key='master_pending'")                    # canceled
sql("INSERT INTO settings (key, value) VALUES ('rotate_due', '2026-11-01')")
r = c.post('/vault/restore', data={'go': 'check', 'source': name, 'secret': secret}, **ip())
print('check', r.status_code)
rv = ' '.join(re.sub(r'<[^>]+>', ' ', c.get('/vault/restore?step=review').get_data(as_text=True)).split())
print('notes', 'That wait is not carried over' in rv, 'still asks you to rotate it' in rv)
wait()                                                                    # asked for on another device meanwhile
r = c.post('/vault/restore', data={'go': 'restore', 'password': TEST_PASSWORD}, **ip())
print('apply refused', r.status_code, 'waiting to take effect' in r.get_data(as_text=True), datakey.is_unlocked(),
      not R.aside_root().exists())
sql("DELETE FROM settings WHERE key='master_pending'")
r = c.post('/vault/restore', data={'go': 'check', 'source': name, 'secret': secret}, **ip())
r = c.post('/vault/restore', data={'go': 'restore', 'password': TEST_PASSWORD}, **ip())
print('done', r.status_code)
signin()
print('after', [r_[0] for r_ in sql("SELECT key FROM settings WHERE key IN ('master_pending', 'rotate_due') ORDER BY key")])
print('history', bool(sql("SELECT 1 FROM audit WHERE action LIKE '%waiting in the backup was not carried over%'")))
""", data)
    assert "refused 400 True True" in out, out
    assert "check 302" in out and "notes True True" in out, out
    assert "apply refused 409 True True True" in out, out
    assert "done 200" in out and "after ['rotate_due']" in out and "history True" in out, out

def test_set_aside_copies_expire_after_30_days_and_today_warns(tmp_path):
    data = _install(tmp_path, str(tmp_path / "vault"))
    out = run(SETUP + """
import datetime as dt
today = dt.date.today()
def aside(days):
    d_ = R.aside_root() / ((dt.datetime.now() - dt.timedelta(days=days)).strftime('%Y-%m-%d-%H%M%S'))
    d_.mkdir(parents=True); (d_ / 'crm.db').write_bytes(b'x'); (d_ / 'keyring.json').write_text('{}')
    return d_
new, soon, old = aside(3), aside(27), aside(31)
print('soon', [(e['made'] == today - dt.timedelta(days=27), (e['gone'] - today).days) for e in R.expiring(today)])
t = ' '.join(re.sub(r'<[^>]+>', ' ', c.get('/').get_data(as_text=True)).split())
gone = (today + dt.timedelta(days=3)).strftime('%B ') + str((today + dt.timedelta(days=3)).day)
print('today', ('is removed on ' + gone) in t, t.count('set aside at the restore on'))
print('expired', R.expire(today), new.exists(), soon.exists(), old.exists())
print('later', R.expire(today + dt.timedelta(days=3)), soon.exists(), new.exists())
""", data)
    assert "soon [(False, 0), (True, 3)]" in out, out
    assert "today True 2" in out, out
    assert "expired 1 True True False" in out and "later 1 False True" in out, out
