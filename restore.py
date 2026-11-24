import hmac
import os
import re
import secrets
import shutil
import tarfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import datakey
import db as D

TTL = 900
PENDING = {}
_GUARD = threading.Lock()
NAME_OK = re.compile(r"^db/[A-Za-z0-9._-]{1,120}\.age$")
STAMP_OK = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}$")
MOVED = ("crm.db", "crm.db-wal", "crm.db-shm", "keyring.json", "storage.json")
COPIED = ("deletions.json", "doc-numbers.json", "deletions.unverified.json")
FACES_KIND = {"db": "faces", "manual": "manual-faces", "upgrade": "upgrade-faces",
              "reset": "reset-faces"}
KEEP_DAYS = 30
WARN_DAYS = 5
BACKUP_KINDS = ("db", "faces", "manual", "manual-faces", "upgrade", "upgrade-faces", "reset", "reset-faces", "found")

class Refused(Exception):
    pass

def data_dir():
    return Path(datakey.KEYRING).parent

def stage_dir():
    return data_dir() / "restore-check"

def upload_dir():
    return data_dir() / "restore-upload"

def aside_root():
    return data_dir() / "set-aside"

def _drop_stage():
    for p_ in (stage_dir(), upload_dir()):
        shutil.rmtree(p_, ignore_errors=True)

def forget():

    with _GUARD:
        PENDING.clear()
    _drop_stage()

datakey.ON_LOCK.append(lambda: PENDING.clear())

def take(tok):

    with _GUARD:
        p_ = PENDING.get(tok or "")
        if p_ and time.time() - p_["at"] > TTL:
            PENDING.pop(tok, None)
            p_ = None
            _drop_stage()
    return p_

def folder_backups(folder):

    root = os.path.abspath(folder)
    try:
        with os.scandir(os.path.join(root, "db")) as it:
            names = sorted("db/" + e.name for e in it
                           if not e.name.startswith(".") and NAME_OK.match("db/" + e.name) and e.is_file())
    except (OSError, ValueError):
        return []
    out = []
    for n in names:
        p_ = os.path.join(root, *n.split("/"))
        try:
            st = os.stat(p_)
        except OSError:
            continue
        out.append(dict(name=n, created=datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                        size=st.st_size, what="In the folder", _ns=st.st_mtime_ns))
    out.sort(key=lambda r: (r["_ns"], r["name"]), reverse=True)
    for r in out:
        del r["_ns"]
    return out

def folder_choices():

    import setup as S_
    out = []
    for p_ in (S_.VAULT_MOUNT, str(data_dir() / "vault")):
        if p_ and os.path.isdir(os.path.join(p_, "db")) and p_ not in out:
            out.append(p_)
    return out

def set_aside_copies():

    root = aside_root()
    if not root.is_dir():
        return []
    out = []
    for d_ in sorted(root.iterdir(), reverse=True):
        if d_.is_dir() and STAMP_OK.match(d_.name) and (d_ / "crm.db").exists() and (d_ / "keyring.json").exists():
            out.append(dict(stamp=d_.name, when=datetime.strptime(d_.name, "%Y-%m-%d-%H%M%S").strftime("%Y-%m-%d %H:%M"),
                            path=str(d_)))
    return out

def aside_day(stamp):
    return datetime.strptime(stamp, "%Y-%m-%d-%H%M%S").date()

def expiring(today, days=WARN_DAYS):

    root = aside_root()
    if not root.is_dir():
        return []
    out = []
    for d_ in sorted(root.iterdir()):
        if d_.is_dir() and STAMP_OK.match(d_.name):
            gone = aside_day(d_.name) + timedelta(days=KEEP_DAYS)
            if gone - today <= timedelta(days=days):
                out.append(dict(stamp=d_.name, made=aside_day(d_.name), gone=max(gone, today)))
    return out

def expire(today):

    root = aside_root()
    n = 0
    for d_ in (sorted(root.iterdir()) if root.is_dir() else []):
        if d_.is_dir() and STAMP_OK.match(d_.name) and aside_day(d_.name) + timedelta(days=KEEP_DAYS) <= today:
            shutil.rmtree(d_, ignore_errors=True)
            n += not d_.exists()
    return n

def faces_partner(con, name):

    try:
        r = con.execute("SELECT MAX(kind), MAX(id) FROM vault_objects WHERE name=?", (name,)).fetchone()
        if not r or r[0] not in FACES_KIND:
            return None
        nxt = con.execute("SELECT MIN(id) FROM vault_objects WHERE kind=? AND id>?", (r[0], r[1])).fetchone()[0]
        f_ = con.execute("SELECT name FROM vault_objects WHERE kind=? AND id>? AND id<? AND deleted IS NULL "
                         "ORDER BY id LIMIT 1", (FACES_KIND[r[0]], r[1], nxt or 1 << 62)).fetchone()
        if not f_ and r[0] == "reset":
            from vault import web as W
            return W.reset_faces_before_161(con).get(name)
    except Exception:
        return None
    return f_[0] if f_ else None

def folder_partners(folder, name):

    def at(n):
        try:
            return os.stat(os.path.join(folder, *n.split("/"))).st_mtime_ns
        except OSError:
            return None
    rows = sorted((t, r["name"]) for r in folder_backups(folder) if (t := at(r["name"])) is not None)
    t0 = next((t for t, n in rows if n == name), None)
    if t0 is None:
        return []
    same = [n for t, n in rows if t == t0 and n != name]
    later = [n for t, n in rows if t0 < t <= t0 + 600 * 10**9][:1]
    return same + later

def open_faces(name, secret, backend, stage, sha=None):

    from vault import seal as S
    raw = str(Path(stage) / "faces.out")
    try:
        S.open_file(name, secret, backend, raw, expected_sha=sha)
        if not tarfile.is_tarfile(raw):
            return False
        with tarfile.open(raw) as tf:
            ms = tf.getmembers()
            if not ms or any(not (m.name == "faces" or m.name.startswith("faces/")) or not (m.isfile() or m.isdir())
                             for m in ms):
                return False
            tf.extractall(stage, filter="data")
        return (Path(stage) / "faces").is_dir()
    except S.Mismatch:
        raise
    except Exception:
        shutil.rmtree(Path(stage) / "faces", ignore_errors=True)
        return False
    finally:
        for f_ in (raw, raw + ".sealed"):
            try:
                os.unlink(f_)
            except OSError:
                pass

def _has(con, table):
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())

def _one(con, sql, table):
    if not _has(con, table):
        return None
    try:
        return con.execute(sql).fetchone()[0]
    except Exception:
        return None

ONLINE = """SELECT g.session_id, s.gallery_id, g.host_fp, g.link_was FROM galleries g JOIN sessions s ON s.id=g.session_id
            WHERE s.gallery_id IS NOT NULL AND s.gallery_id != '' AND (g.upload_state IN ('uploading', 'awaiting')
            OR (g.upload_state='up' AND g.removed_at IS NULL))"""

def online(con):

    if not (_has(con, "galleries") and _has(con, "sessions")):
        return {}
    try:
        rows = con.execute(ONLINE).fetchall()
    except Exception:
        try:
            rows = con.execute("""SELECT g.session_id, s.gallery_id, '' AS host_fp, '' AS link_was FROM galleries g
                                  JOIN sessions s ON s.id=g.session_id WHERE g.upload_state='up' AND s.gallery_id != ''""").fetchall()
        except Exception:
            return {}
    return {r[1]: (r[0], r[2] or "", r[3] or "") for r in rows}

def summary(con):

    def day(v):
        return str(v)[:10] if v else ""
    return dict(
        clients=_one(con, "SELECT COUNT(*) FROM households WHERE COALESCE(demo, 0)=0", "households"),
        sessions=_one(con, "SELECT COUNT(*) FROM sessions s JOIN households h ON h.id=s.household_id "
                           "WHERE COALESCE(h.demo, 0)=0", "sessions"),
        galleries=len(online(con)),
        payments=_one(con, "SELECT COUNT(*) FROM payments", "payments"),
        newest_client=day(_one(con, "SELECT MAX(created) FROM households WHERE COALESCE(demo, 0)=0", "households")),
        newest_session=day(_one(con, "SELECT MAX(created) FROM sessions", "sessions")),
        last_change=str(_one(con, "SELECT MAX(at) FROM audit", "audit") or "")[:16],
        photos=_one(con, "SELECT COUNT(*) FROM photo_files WHERE session_id IS NOT NULL", "photo_files"),
        schema=con.execute("PRAGMA user_version").fetchone()[0])

def photo_ids(con):

    if not _has(con, "photo_files"):
        return set()
    return {r[0] for r in con.execute("SELECT id FROM photo_files")}

def other_ids(con):

    if not _has(con, "photo_files"):
        return set()
    return {r[0] for r in con.execute("SELECT id FROM photo_files WHERE session_id IS NULL")}

def _file_sha(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f_:
        while chunk := f_.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()

def upload_partner_sha(connect, db_path):

    from vault import backup as BK
    if connect is None:
        return None
    sha = _file_sha(db_path)
    c_ = connect()
    try:
        r = c_.execute("SELECT name FROM vault_objects WHERE sha256=? AND deleted IS NULL ORDER BY id DESC LIMIT 1",
                       (sha,)).fetchone()
        fname = r and faces_partner(c_, r[0])
        return fname and (BK.recorded_sha(c_, fname, "local") or BK.recorded_sha(c_, fname))
    except Exception:
        return None
    finally:
        c_.close()

def check(source, secret, *, connect=None, schema_now, fresh, folder="", upload=None, stamp="", faces_upload=None):

    from vault import backends as B, backup as BK, seal as S
    forget()
    if not fresh:
        _no_master_waiting(connect)
    stage = stage_dir()
    sha, checked, label, faces, fname, fsha, more = None, False, "", "", None, None, []
    try:
        if source == "aside":
            if not STAMP_OK.match(stamp or "") or not (aside_root() / stamp / "crm.db").exists():
                raise Refused("That copy isn't there any more. Choose another.")
            stage.mkdir(mode=0o700)
            for f_ in ("crm.db", "crm.db-wal", "crm.db-shm", "keyring.json", "storage.json"):
                if (aside_root() / stamp / f_).exists():
                    shutil.copy2(aside_root() / stamp / f_, stage / f_)
            if (aside_root() / stamp / "faces").is_dir():
                shutil.copytree(aside_root() / stamp / "faces", stage / "faces")
                faces = "restored"
            checked, label = True, "the data set aside on " + stamp[:10]
        else:
            if source == "upload":
                if upload is None or not upload.filename:
                    raise Refused("Choose the backup file first.")
                up = upload_dir() / "db"
                up.mkdir(parents=True, mode=0o700)
                upload.save(str(up / "upload.age"))
                backend, name = B.LocalBackend(str(upload_dir()), read_only=True), "db/upload.age"
                label = "the file " + (os.path.basename(upload.filename)[:80] or "you chose")
                faces = "upload"
                if faces_upload is not None and faces_upload.filename:
                    faces_upload.save(str(up / "faces.age"))
                    fname, faces = "db/faces.age", "upload-chosen"
                    if not fresh:
                        fsha = upload_partner_sha(connect, str(up / "upload.age"))
            elif source.startswith("folder:"):
                name = source[7:]
                if not NAME_OK.match(name) or not folder or not os.path.isdir(folder):
                    raise Refused("Choose a backup from the list.")
                backend = B.LocalBackend(folder, read_only=True)
                label = "the backup " + name[3:15] + "... in " + folder
                fname, *more = folder_partners(folder, name) or [None]
            else:
                if fresh or not NAME_OK.match(source or ""):
                    raise Refused("Choose a backup from the list.")
                name = source
                backend = None
                c_ = connect()
                try:
                    sha = BK.recorded_sha(c_, name, "local") or BK.recorded_sha(c_, name)
                    row = c_.execute("SELECT MIN(created) FROM vault_objects WHERE name=?", (name,)).fetchone()
                    fname = faces_partner(c_, name)
                    fsha = fname and (BK.recorded_sha(c_, fname, "local") or BK.recorded_sha(c_, fname))
                finally:
                    c_.close()
                label = "the backup of " + (row[0][:16].replace("T", " ") if row and row[0] else name)
            checked = bool(sha)
            try:
                BK.restore(name, secret, str(stage), backend=backend, expected_sha=sha)
            except S.Mismatch:
                raise Refused("This backup is not the one the app made: its fingerprint differs from the one recorded "
                              "when it was made. It was not opened. Choose another backup.")
            except FileNotFoundError:
                raise Refused("That backup file isn't in the backup folder any more. Choose another.")
            except ValueError as e:
                words = str(e)
                if "unexpected entry" in words and "faces" in words:
                    raise Refused("That file holds only face photos (each backup has a second, smaller file for "
                                  "them). Choose the one made at the same time.")
                if "not a Lumen Aureum backup" in words:
                    raise Refused("That file is not a Lumen Aureum backup.")
                if "integrity" in words:
                    raise Refused("That backup opened, but its database is damaged. Choose another backup.")
                raise Refused(_key_words())
            except Exception:
                raise Refused(_key_words())
            for f_ in ("settings.json",):
                (stage / f_).unlink(missing_ok=True)
            if fname:
                from vault import web as W
                try:
                    faces = "restored" if any(open_faces(f_, secret, backend or W.store_backends()[0], stage, sha=fsha)
                                              for f_ in (fname, *more)) \
                        else ("upload-unopened" if faces == "upload-chosen" else "unopened")
                except S.Mismatch:
                    if faces == "upload-chosen":
                        raise Refused("The face photos file you chose is not the one the app made with this backup: "
                                      "its fingerprint differs from the one recorded. Nothing was restored. Choose the "
                                      "face photos file made at the same time, or leave it out.")
                    raise Refused("The face photos file made with this backup is not the one the app made: its "
                                  "fingerprint differs from the one recorded. Nothing was restored. Choose another backup.")
            elif not faces:
                faces = "none"
        try:
            key = datakey.unwrap_master(secret, ring_path=str(stage / "keyring.json"))
        except ValueError:
            raise Refused("That master key doesn't open the key inside this backup. Use the master key the studio "
                          "had when the backup was made.")
        from vault import keys as K
        con = D.connect(stage / "crm.db", key=key)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise Refused("That copy opened, but its database is damaged. Choose another backup.")
            then = summary(con)
            then_online = online(con)
            then_photos = photo_ids(con)
            then_master = _quiet(lambda: K.master_pub(con))
            then_waiting = bool(_quiet(lambda: _setting(con, "master_pending")))
        finally:
            con.close()
        if then["schema"] > schema_now:
            raise Refused("This backup was made by a newer version of Lumen Aureum. Update the app to that version "
                          "or later first, then restore it.")
        now, now_online, gone_photos, gone_other, older_key, rotate = None, {}, 0, 0, False, False
        if not fresh:
            c_ = connect()
            try:
                now, now_online = summary(c_), online(c_)
                gone = photo_ids(c_) - then_photos
                gone_other = len(gone & other_ids(c_))
                gone_photos = len(gone) - gone_other
                now_master = _quiet(lambda: K.master_pub(c_))
                older_key = bool(then_master and now_master and then_master != now_master)
                rotate = bool(_quiet(lambda: _setting(c_, "rotate_due")))
            finally:
                c_.close()
        tok = secrets.token_urlsafe(16)
        with _GUARD:
            PENDING[tok] = dict(id=secrets.token_hex(8), stage=str(stage), key=key, label=label, checked=checked, then=then, now=now,
                                down=sorted(set(now_online) - set(then_online)) if not fresh else [],
                                marked=sorted(set(then_online) - set(now_online)) if not fresh else [],
                                gone_photos=gone_photos, gone_other=gone_other, at=time.time(), fresh=fresh, faces=faces,
                                older_key=older_key, rotate=rotate, waiting=then_waiting)
        return tok
    except BaseException:
        _drop_stage()
        raise
    finally:
        shutil.rmtree(upload_dir(), ignore_errors=True)

def _quiet(fn):
    try:
        return fn()
    except Exception:
        return None

def _setting(con, k):
    r = con.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r[0] if r else None

def _no_master_waiting(connect):

    import auth
    c_ = connect()
    try:
        p_ = auth.master_pending(c_)
    finally:
        c_.close()
    if p_:
        raise Refused(f"A new master key is waiting to take effect on {p_['when']}. Restore after it takes effect, or "
                      "cancel it first in Settings, Security. Nothing was changed.")

def _key_words():
    return ("That master key doesn't open this backup. A backup made before a master key change needs the key the "
            "studio had then.")

def _reconcile(p_, connect):

    con = D.connect(Path(p_["stage"]) / "crm.db", key=p_["key"])
    try:
        today = date.today().isoformat()
        if not p_["fresh"]:
            cur = connect()
            try:
                now_online = online(cur)
                left = cur.execute("SELECT gallery_id, host_fp, added FROM gallery_leftovers").fetchall() \
                    if _has(cur, "gallery_leftovers") else []
                vobj = cur.execute("SELECT kind, name, backend, size, sha256, created, deleted FROM vault_objects "
                                   "WHERE kind IN (%s)" % ",".join("?" * len(BACKUP_KINDS)), BACKUP_KINDS).fetchall() \
                    if _has(cur, "vault_objects") else []
            finally:
                cur.close()
            then_online = online(con)
            con.execute("""CREATE TABLE IF NOT EXISTS gallery_leftovers (
                gallery_id TEXT NOT NULL, host_fp TEXT NOT NULL, added TEXT, PRIMARY KEY (gallery_id, host_fp))""")
            for gid, (sid, fp, was) in now_online.items():
                if gid not in then_online and fp:
                    con.execute("INSERT OR IGNORE INTO gallery_leftovers (gallery_id, host_fp, added) VALUES (?,?,?)",
                                (gid, fp, today))
                    if was and was != gid:
                        con.execute("INSERT OR IGNORE INTO gallery_leftovers (gallery_id, host_fp, added) VALUES (?,?,?)",
                                    (was, fp, today))
            for r in left:
                con.execute("INSERT OR IGNORE INTO gallery_leftovers (gallery_id, host_fp, added) VALUES (?,?,?)", tuple(r))
            for gid, (sid, fp, was) in then_online.items():
                if gid not in now_online:
                    try:
                        con.execute("UPDATE galleries SET removed_at=?, upload_state='removed', close_on=NULL "
                                    "WHERE session_id=?", (today, sid))
                    except Exception:
                        con.execute("UPDATE galleries SET removed_at=?, upload_state='removed' WHERE session_id=?",
                                    (today, sid))
            if _has(con, "vault_objects"):
                known = {(r[0], r[1]) for r in con.execute("SELECT name, backend FROM vault_objects")}
                for r in vobj:
                    if (r[1], r[2]) not in known:
                        con.execute("INSERT INTO vault_objects (session_id, kind, name, backend, size, sha256, created, "
                                    "deleted) VALUES (NULL,?,?,?,?,?,?,?)", tuple(r))
        if _has(con, "settings"):

            con.execute("DELETE FROM settings WHERE key='master_pending'")
            if p_.get("rotate"):
                con.execute("INSERT INTO settings (key, value) VALUES ('rotate_due', ?) ON CONFLICT(key) DO NOTHING",
                            (today,))
        if _has(con, "audit"):
            con.execute("INSERT INTO audit (at, action, who) VALUES (?,?,?)",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         f"Restored {p_['label']}" + (f"; the data before it is in set-aside/{p_['stamp']} in the data folder"
                                                       if p_.get("stamp") else "")
                         + ("; the face photos came back with it" if p_.get("faces") == "restored" else "")
                         + ("; the new master key that was waiting in the backup was not carried over"
                            if p_.get("waiting") else ""), "Owner"))
        con.commit()
    finally:
        con.close()

def _readme(dest, label):
    (dest / "README.txt").write_text(
        f"Lumen Aureum set this data aside on {datetime.now():%Y-%m-%d %H:%M}, when it restored {label}.\n\n"
        "It is the whole studio as it was just before: crm.db (the data, encrypted), keyring.json (its key: your\n"
        "password then, or your master key, opens it), storage.json and the face photos. Nothing was deleted.\n\n"
        f"Lumen Aureum removes this folder {KEEP_DAYS} days after it was set aside, and Today says so a few days before.\n"
        "To keep it longer, copy it somewhere else first.\n\n"
        "To go back to it: Settings, Backups, Restore from a backup, then choose this copy under 'Data set aside at an earlier restore'.\n"
        "Or by hand: stop the app, move these files back into the data folder, start it, sign in.\n")

def apply(tok, connect, restart, fresh_done=None):

    from vault import backup as BK
    import jobs
    import locks
    p_ = take(tok)
    if not p_:
        raise Refused("That check has expired. Check the backup again.")
    if not p_["fresh"]:
        _no_master_waiting(connect)
    busy = [j_["label"] for j_ in list(jobs.JOBS.values()) if j_.get("state") == "running"]
    if busy:
        raise Refused(f"Wait until this finishes: {busy[0]}. Then restore.")
    if not all((Path(p_["stage"]) / f_).is_file() for f_ in ("crm.db", "keyring.json")):
        raise Refused("That check has expired. Check the backup again.")
    data = data_dir()
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    dest = aside_root() / stamp if not p_["fresh"] else None
    p_["stamp"] = stamp if dest else ""
    if not BK._ONE_AT_A_TIME.acquire(timeout=30):
        raise Refused("A backup is running. Restore again in a minute.")
    try:
        if not locks.WRITES.acquire(timeout=60):
            raise Refused("The app is busy saving. Restore again in a minute.")
        try:
            _reconcile(p_, connect)
            if not p_["fresh"]:
                try:
                    c_ = connect()
                    try:
                        if _has(c_, "audit"):
                            c_.execute("INSERT INTO audit (at, action, who) VALUES (?,?,?)",
                                       (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        f"Restored {p_['label']}; this data was set aside in set-aside/{stamp}", "Owner"))
                            c_.commit()
                        c_.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    finally:
                        c_.close()
                except Exception:
                    pass
            datakey.lock()
            moved = []
            try:
                if dest:
                    dest.mkdir(parents=True, mode=0o700)
                    for f_ in MOVED:
                        if (data / f_).exists():
                            os.replace(data / f_, dest / f_)
                            moved.append(f_)
                    for f_ in COPIED:
                        if (data / f_).exists():
                            shutil.copy2(data / f_, dest / f_)
                stage = Path(p_["stage"])
                faces = data / "faces"
                if (stage / "faces").is_dir():
                    if faces.exists():
                        keep = dest or aside_root() / stamp
                        keep.mkdir(parents=True, exist_ok=True, mode=0o700)
                        os.replace(faces, keep / "faces")
                        moved.append("faces")
                    os.replace(stage / "faces", faces)
                elif dest and faces.is_dir():
                    shutil.copytree(faces, dest / "faces")
                if dest:
                    _readme(dest, p_["label"])
                if not (stage / "storage.json").exists() and dest and (dest / "storage.json").exists():
                    shutil.copy2(dest / "storage.json", stage / "storage.json")
                for f_ in os.listdir(stage):
                    if f_ in MOVED or f_.endswith(".restored.json"):
                        os.replace(stage / f_, data / f_)
            except BaseException:
                keep = dest or aside_root() / stamp
                if "faces" in moved and (keep / "faces").exists():
                    shutil.rmtree(data / "faces", ignore_errors=True)
                    os.replace(keep / "faces", data / "faces")
                for f_ in MOVED:
                    if dest and (dest / f_).exists() and f_ in moved:
                        os.replace(dest / f_, data / f_)
                raise
        finally:
            locks.WRITES.release()
    finally:
        BK._ONE_AT_A_TIME.release()
    forget()
    if p_["fresh"] and fresh_done:
        fresh_done()
    restart()
    return str(dest) if dest else None

def password_ok(pw):

    import auth
    try:
        k_ = auth.unwrap_password_limited(pw or "")
    except (auth.Busy, ValueError):
        return False
    try:
        return bool(k_) and hmac.compare_digest(k_, datakey.dk())
    except datakey.Locked:
        return False

def same_password(tok, pw):

    p_ = take(tok)
    if not p_ or not pw:
        return False
    try:
        return datakey.unwrap_password(pw, Path(p_["stage"]) / "keyring.json") is not None
    except (ValueError, OSError):
        return False

def register(app, connect, audit, *, schema_now, soft_restart, end_sessions):

    from flask import abort, redirect, render_template, request, session, url_for
    import auth
    import netsec

    def restart():

        end_sessions()
        soft_restart()
        import sys
        if "gunicorn" in sys.modules and not os.environ.get("CRM_TESTING"):
            import signal

            def later():
                time.sleep(1.5)
                os.kill(os.getpid(), signal.SIGTERM)
            threading.Thread(target=later, name="restore-restart", daemon=True).start()

    def page(**kw):
        fresh = kw.get("fresh")
        kw.setdefault("layout", "auth_base.html" if fresh else "base.html")
        kw.setdefault("err", None)
        kw.setdefault("need_code", False)
        kw.setdefault("unchecked_words", _unchecked())
        r = app.make_response(render_template("restore.html", **kw))
        r.headers["Cache-Control"] = "no-store"
        r.headers["Referrer-Policy"] = "same-origin"
        return r

    def choose(fresh, err=None, status=200):
        from vault import web as W
        if fresh:
            folder = (request.values.get("folder") or "").strip()[:300]
            folders = folder_choices()
            folder = folder or (folders[0] if folders else "")

            listed = bool(folder) and (folder in folders or session.get("restore_folder") == folder)
            items = folder_backups(folder) if listed else []
            r = page(fresh=True, step="choose", folder=folder, folders=folders, items=items, asides=[], err=err,
                     need_code=bool(folder) and not listed)
        else:
            r = page(fresh=False, step="choose", items=W.backups(connect, reset=True), asides=set_aside_copies(), err=err,
                     folder="", folders=[])
        r.status_code = status
        return r

    def claim_ok():
        return hmac.compare_digest(re.sub(r"[^0-9A-Z]", "", request.form.get("claim", "").upper()),
                                   re.sub(r"[^0-9A-Z]", "", netsec.claim_code()))

    def do_list():

        th = app.config.get("auth_throttle")
        ip_ = request.remote_addr or ""
        if th is not None and th.ip_blocked(ip_):
            return choose(True, f"Too many wrong tries from here. Try again {th.wait_text(ip_)}.", 429)
        if not claim_ok():
            if th is not None:
                th.fail(ip_, "claim code")
            return choose(True, "Wrong setup code. A restart makes a new one.", 400)
        session["restore_folder"] = (request.form.get("folder") or "").strip()[:300]
        return choose(True)

    def do_check(fresh):
        th = app.config.get("auth_throttle") if fresh else app.config.get("key_throttle")
        ip_ = request.remote_addr or ""
        if th is not None and th.ip_blocked(ip_):
            return choose(fresh, f"Too many wrong tries from here. Try again {th.wait_text(ip_)}.", 429)
        if fresh and not claim_ok():
            if th is not None:
                th.fail(ip_, "claim code")
            return choose(fresh, "Wrong setup code. A restart makes a new one.", 400)
        secret = (request.form.get("secret") or "").strip()
        if not secret.startswith("AGE-SECRET-KEY-1") or len(secret) > 200:
            return choose(fresh, "Paste the whole master key (it starts AGE-SECRET-KEY-1).", 400)
        src = request.form.get("source") or ""
        stamp = ""
        if src.startswith("aside:") and not fresh:
            src, stamp = "aside", src[6:]
        up = request.files.get("file")
        if up is not None and up.filename:
            src = "upload"
        try:
            tok = check(src, secret, connect=connect, schema_now=schema_now, fresh=fresh,
                        folder=(request.form.get("folder") or "").strip(), upload=up,
                        stamp=stamp, faces_upload=request.files.get("faces"))
        except Refused as e:
            if th is not None and str(e) == _key_words():
                th.fail(ip_, "master key (restore)")
            return choose(fresh, str(e), 400)
        session["restore_tok"] = tok
        if not fresh:
            audit("Checked a backup to restore")
        return redirect(url_for("setup_restore" if fresh else "vault_restore", step="review"))

    def review(fresh, err=None, status=200):
        p_ = take(session.get("restore_tok"))
        if not p_ or p_["fresh"] != fresh:
            return choose(fresh, "That check has expired. Check the backup again." if session.pop("restore_tok", None) else None)
        r = page(fresh=fresh, step="review", p=p_, err=err, data_dir=str(data_dir()))
        r.status_code = status
        return r

    def do_apply(fresh):
        tok = session.get("restore_tok")
        p_ = take(tok)
        if not p_ or p_["fresh"] != fresh:
            return choose(fresh, "That check has expired. Check the backup again.", 400)
        sent = request.form.get("check")
        if sent is not None and not hmac.compare_digest(sent, p_["id"]):
            return choose(fresh, "Another backup was checked after this one, so nothing was restored. Check the backup "
                                 "again.", 409)
        pw = request.form.get("password", "")
        same = False
        if not fresh:
            th = app.config.get("auth_throttle")
            ip_ = request.remote_addr or ""
            if th is not None and th.ip_blocked(ip_):
                return review(fresh, f"Too many wrong tries from here. Try again {th.wait_text(ip_)}.", 429)
            if not password_ok(pw):
                if th is not None:
                    th.fail(ip_, "password (restore)")
                return review(fresh, "That isn't your password. Nothing was changed.", 400)
            same = same_password(tok, pw)
        from flask import g
        if "db" in g:
            g.db.commit()
            g.pop("db").close()
        try:
            where = apply(tok, connect, restart, fresh_done=netsec.forget_claim_code if fresh else None)
        except Refused as e:
            return review(fresh, str(e), 409)
        session.clear()
        return page(fresh=fresh, step="done", where=where, same=same, label=p_["label"], layout="auth_base.html")

    @app.route("/setup/restore", methods=["GET", "POST"], endpoint="setup_restore")
    def setup_restore():
        if datakey.exists():
            abort(404)
        if request.method == "POST":
            if request.form.get("go") == "restore":
                return do_apply(True)
            if request.form.get("go") == "check":
                return do_check(True)
            if request.form.get("go") == "list":
                return do_list()
        if request.args.get("step") == "review":
            return review(True)
        return choose(True)

    @app.route("/vault/restore", methods=["GET", "POST"], endpoint="vault_restore")
    def vault_restore():
        if request.method == "POST":
            if request.form.get("go") == "restore":
                return do_apply(False)
            if request.form.get("go") == "check":
                return do_check(False)
        if request.args.get("step") == "review":
            return review(False)
        return choose(False)

    auth.OPEN.add("setup_restore")
    app.config.setdefault("quiet_endpoints", set()).add("vault_restore")

def _unchecked():
    from vault import cli
    return cli.UNCHECKED.format(what="backup")
