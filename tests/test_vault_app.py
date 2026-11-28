import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def mark(*dirs):
    for d in dirs:
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, ".vault-root"), "a").close()

def run(code, tmp):
    env = dict(os.environ, CRM_DB=str(tmp / "crm.db"), CRM_STORAGE_CFG=str(tmp / "storage.json"),
               CRM_PHOTOS_ROOT=str(tmp / "ph"), CRM_GALLERY_ROOT=str(tmp / "gl"), CRM_GALLERY_SYNC="0")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return r.stdout

def test_backups_prune_restore_reset(tmp_path):
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(tmp_path / "store")}}))
    mark(tmp_path / "store")
    out = run("""
import sys; sys.path.insert(0, 'tests'); from helpers import unlock_new_install
import app as A
secret = unlock_new_install(A)
from vault import keys as K, backup as BK
con = A.connect()
con.execute("INSERT INTO households (name) VALUES ('The Harlows')")
con.execute("INSERT INTO sessions (household_id, type) VALUES (1, 'senior')"); con.commit()
K.session_pub(con, 1)
con.close()
(A.FACES).mkdir(parents=True, exist_ok=True); (A.FACES / '1.jpg').write_bytes(b'face')
for i in range(4):
    BK.sealed_backup(str(A.DB_PATH), str(A.FACES), keep=2)
con = A.connect()
print('live', con.execute("SELECT COUNT(DISTINCT name) FROM vault_objects WHERE kind='db' AND deleted IS NULL").fetchone()[0])
name = con.execute("SELECT name FROM vault_objects WHERE kind='db' AND deleted IS NULL ORDER BY id DESC LIMIT 1").fetchone()[0]
con.close()
import os
blob = open(os.path.join(A.vault_web.config()['primary']['path'], name), 'rb').read()
print('plain in blob', b'Harlows' in blob)
dest = str(A.DB_PATH.parent / 'restored')
BK.restore(name, secret, dest)
import datakey, db
k = datakey.unwrap_master(secret, ring_path=dest + '/keyring.json')
print('restored', db.connect(dest + '/crm.db', key=k).execute('SELECT name FROM households').fetchone()[0])
print('restored plaintext', any(b'Harlows' in open(os.path.join(dest, f), 'rb').read() for f in os.listdir(dest)))
from helpers import signed_in_client; c = signed_in_client(A)
r = c.post('/settings/reset', data={'confirm': 'RESET'}, environ_base={'REMOTE_ADDR': '127.0.0.1'})
con = A.connect()
print('after reset households', con.execute('SELECT COUNT(*) FROM households').fetchone()[0],
      'reset objects', con.execute("SELECT COUNT(*) FROM vault_objects WHERE kind='reset'").fetchone()[0],
      'faces objects', con.execute("SELECT COUNT(*) FROM vault_objects WHERE kind='reset-faces'").fetchone()[0],
      'keys kept', con.execute("SELECT COUNT(*) FROM vault_keys WHERE orphaned IS NOT NULL").fetchone()[0],
      'faces dir', A.FACES.exists())
""", tmp_path)
    assert "live 2" in out, out
    assert "plain in blob False" in out, out
    assert "restored The Harlows" in out and "restored plaintext False" in out, out
    assert "after reset households 0 reset objects 1 faces objects 1 keys kept 1 faces dir False" in out, out

def test_gallery_host_local_and_sftp(tmp_path):
    hosts = [{"type": "local", "path": str(tmp_path / "web")}]
    if os.environ.get("VAULT_SFTP_KEY"):
        hosts.append({"type": "sftp", "host": "127.0.0.1", "user": os.environ["USER"], "key_file": os.environ["VAULT_SFTP_KEY"],
                      "root": str(tmp_path / "sftpweb"), "known_hosts": os.environ["VAULT_SFTP_KNOWN"]})
    mark(tmp_path / "store", tmp_path / "web")
    for h in hosts:
        (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(tmp_path / "store")},
                                                           "gallery_host": h}))
        (tmp_path / "src").mkdir(exist_ok=True)
        from PIL import Image
        Image.new("RGB", (64, 48), (90, 120, 200)).save(tmp_path / "src" / "a.jpg")
        out = run("""
import sys; sys.path.insert(0, 'tests'); from helpers import unlock_new_install
import app as A
secret = unlock_new_install(A)
import pathlib
G = A.app.view_functions['upload_gallery_req'].__globals__
k = G['new_key'](); G['build']('gtest1', pathlib.Path(%r), k, picks=dict(retouch=5, market=2, email='x@y', ref='gtest1'))
print('uploaded', G['upload_gallery']('gtest1', 5, '2027-11-11'))
host = G['gallery_host']()
print('index on host', host.stat('gtest1/index.html') is not None, 'htaccess', host.stat('gtest1/.htaccess') is not None)
import io as _io; _b = _io.BytesIO(); host.get_stream('gtest1/limits.txt', _b); print('limits', _b.getvalue().decode().split())
print('gate', host.stat('gtest1/gate.php') is not None)
print('seen before', G['seen_at']('gtest1'))
import io; host.put_stream('gtest1/seen.txt', io.BytesIO(b'2026-11-11T12:00:00Z'))   # fixed date: only read back as text
print('seen after', G['seen_at']('gtest1'))
G['delete_gallery']('gtest1')
print('left', host.list('gtest1/'), host.stat('gtest1/.htaccess'))
""" % str(tmp_path / "src"), tmp_path)
        assert "index on host True htaccess True" in out, out
        assert "seen before None" in out and "seen after 2026-11-11T12:00:00Z" in out, out
        assert "left [] None" in out, out

        assert re.search(r"limits \['days=5', 'unopened_until=2027-11-11', 'unopened_ts=\d+', 'warn_days=2', 'proof=1'\]", out), out
        assert "gate True" in out, out

def test_daily_backup_schedule(tmp_path):
    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(tmp_path / "store")}}))
    mark(tmp_path / "store")
    out = run("""
import sys; sys.path.insert(0, 'tests'); from helpers import unlock_new_install
import app as A
secret = unlock_new_install(A)
from datetime import datetime
from vault import keys as K, web as W
con = A.connect()
con.execute("INSERT INTO settings (key, value) VALUES ('backup_time', '21:30')"); con.commit()
d = datetime(2026, 11, 12, 21, 0)
print('before setup', W.run_if_due(A.connect, A.DB_PATH, A.FACES, now=d.replace(minute=31)))
con.execute("INSERT INTO settings (key, value) VALUES ('setup_complete', '1')"); con.commit(); con.close()
print('before time', W.run_if_due(A.connect, A.DB_PATH, A.FACES, now=d))
print('at time', W.run_if_due(A.connect, A.DB_PATH, A.FACES, now=d.replace(minute=31)))
print('again same day', W.run_if_due(A.connect, A.DB_PATH, A.FACES, now=d.replace(minute=45)))
print('next day', W.run_if_due(A.connect, A.DB_PATH, A.FACES, now=datetime(2026, 11, 13, 22, 0)))
""", tmp_path)
    assert "before setup False" in out and "before time False" in out and "at time True" in out and "again same day False" in out and "next day True" in out, out

def test_restore_on_a_new_machine_with_only_the_master_key(tmp_path):

    (tmp_path / "storage.json").write_text(json.dumps({"primary": {"type": "local", "path": str(tmp_path / "store")}}))
    mark(tmp_path / "store")
    out = run("""
import sys; sys.path.insert(0, 'tests'); from helpers import unlock_new_install
import app as A
secret = unlock_new_install(A)
from vault import backup as BK
con = A.connect(); con.execute("INSERT INTO households (name) VALUES ('The Harlows')"); con.commit(); con.close()
print('NAME', BK.sealed_backup(str(A.DB_PATH))[0]); print('SECRET', secret)
""", tmp_path)
    name = out.split("NAME ")[1].split()[0]
    secret = out.split("SECRET ")[1].split()[0]
    import shutil
    copy = tmp_path / "usb-copy"
    shutil.copytree(tmp_path / "store", copy)
    os.remove(copy / ".vault-root")
    new = tmp_path / "newmachine"
    new.mkdir()
    env = dict(os.environ, CRM_DB=str(new / "crm.db"), CRM_STORAGE_CFG=str(new / "storage.json"))
    code = f"""
import getpass; getpass.getpass = lambda prompt='': {secret!r}
from vault import cli
raise SystemExit(cli.main(['restore', {name!r}, {str(new / 'restored')!r}, '--from', {str(copy)!r}]))
"""
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    sys.path.insert(0, ROOT)
    import datakey, db
    k = datakey.unwrap_master(secret, ring_path=str(new / "restored" / "keyring.json"))
    assert db.connect(new / "restored" / "crm.db", key=k).execute("SELECT name FROM households").fetchone()[0] == "The Harlows"
