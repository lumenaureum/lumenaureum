import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _install(tmp, name="data"):
    data, vault = tmp / name, tmp / "vault"
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
import sys, re, os; sys.path.insert(0, 'tests')
from helpers import signed_in_client, TEST_PASSWORD
import app as A, datakey
from vault import backup as BK
c = signed_in_client(A)
ip = {'environ_base': {'REMOTE_ADDR': '127.0.0.1'}}
def sql(q, a=()):
    con = A.connect(); r = con.execute(q, a).fetchall(); con.commit(); con.close(); return [tuple(x) for x in r]
"""

def test_161_refuses_a_database_162_has_opened(tmp_path):

    data = _install(tmp_path)
    out = run(SETUP + """
con = A.connect(); print('user_version', con.execute('PRAGMA user_version').fetchone()[0]); con.close()
print('mark', open(A.SCHEMA_MARK).read().split()[0])
""", data)
    assert "user_version 9" not in out, out + " - 1.6.1 (SCHEMA_VERSION 9) would open this database"
    assert "mark 9" not in out, out

def test_restore_on_a_new_install_does_not_invent_a_failed_face_photos_file(tmp_path):

    data = _install(tmp_path)
    out = run(SETUP + """
sql("INSERT INTO households (name) VALUES ('Client A')")
first = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]
second = BK.sealed_backup(str(A.DB_PATH), None, kind='manual')[0]
print('FIRST', first)
print('SECRET', c.master_secret)
print('FACES', A.FACES.exists() and any(A.FACES.iterdir()))
""", data)
    first = out.split("FIRST ")[1].split()[0]
    secret = out.split("SECRET ")[1].split()[0]
    assert "FACES True" not in out, out
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    out = run(f"""
import re, netsec
import app as A
c = A.app.test_client(); ip = {{'environ_base': {{'REMOTE_ADDR': '127.0.0.1'}}, 'base_url': 'https://localhost', 'headers': {{'Origin': 'https://localhost'}}}}
claim = netsec.claim_code(); folder = {str(tmp_path / 'vault')!r}
c.post('/setup/restore', data={{'go': 'list', 'claim': claim, 'folder': folder}}, **ip)
r = c.post('/setup/restore', data={{'go': 'check', 'claim': claim, 'folder': folder, 'source': 'folder:' + {first!r},
                                    'secret': {secret!r}}}, follow_redirects=True, **ip)
t = ' '.join(re.sub(r'<[^>]+>', ' ', r.get_data(as_text=True)).split())
print('REVIEW', 'What changes' in t, t[-400:] if 'What changes' not in t else '')
print('FALSE_FACES', 'face photos file made with this backup could not be opened' in t)
""", fresh)
    assert "REVIEW True" in out, out
    assert "FALSE_FACES False" in out, out
