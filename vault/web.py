import json
import os
import secrets
import time
from pathlib import Path

from flask import abort, redirect, render_template, request, url_for

from . import backends as B, keys as K, seal as S

CFG_PATH = Path(os.environ.get("CRM_STORAGE_CFG", Path(__file__).resolve().parent.parent / "data" / "storage.json"))
DEFAULT_CFG = {"primary": {"type": "local", "path": str(Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent.parent / "data" / "crm.db")).parent / "vault")}}
PATHS = {}
PENDING = {}
TTL = 600

def config():

    import datakey
    try:
        raw = CFG_PATH.read_bytes()
    except OSError:
        return dict(DEFAULT_CFG)
    if raw.startswith(datakey.FILE_MAGIC):
        return json.loads(datakey.open_bytes(raw, "storage", label="storage"))
    try:
        return json.loads(raw)
    except ValueError:
        return dict(DEFAULT_CFG)

def write_config(cfg):
    import datakey
    datakey.write_sealed(CFG_PATH, json.dumps(cfg, indent=2).encode(), "storage", label="storage")

def store_backends():

    return [B.from_config(config()["primary"])]

def describe(cfg):
    if not cfg:
        return "-"
    t = cfg.get("type")
    return {"local": f"folder {cfg.get('path')}", "sftp": f"SFTP {cfg.get('user')}@{cfg.get('host')}:{cfg.get('root')}",
            "s3": f"S3 bucket {cfg.get('bucket')}{' at ' + cfg['endpoint'] if cfg.get('endpoint') else ''}"}.get(t, t)

def _setting(con, key, default=""):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r and r[0] else default

def _record_run(connect, now):
    con = connect()
    try:
        con.execute("INSERT INTO settings (key, value) VALUES ('backup_last_run', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now.isoformat(timespec="seconds"),))
        con.commit()
    finally:
        con.close()

def run_if_due(connect, db_path, faces_dir, now=None):

    from datetime import datetime
    from . import backup as BK
    con = connect()
    try:
        at = _setting(con, "backup_time", "02:40")
        last = _setting(con, "backup_last_run")
        has_key = bool(K.master_pub(con))
    finally:
        con.close()
    now = now or datetime.now()
    if not has_key or now.strftime("%H:%M") < at or last[:10] == now.date().isoformat():
        return False
    BK.sealed_backup(str(db_path), str(faces_dir) if faces_dir.exists() else None)
    _record_run(connect, now)
    return True

def start_scheduler(app, connect, db_path, faces_dir):

    import threading
    from datetime import datetime
    from . import backup as BK
    PATHS.update(db=Path(db_path), faces=Path(faces_dir))

    def loop():
        time.sleep(90)
        while True:
            try:
                run_if_due(connect, db_path, faces_dir)
            except Exception:
                app.logger.exception("scheduled backup failed")
            time.sleep(60)
    threading.Thread(target=loop, name="vault-backup", daemon=True).start()

def backups(connect, limit=30):

    con = connect()
    try:
        rows = con.execute("""SELECT name, MIN(created) created, MAX(size) size, GROUP_CONCAT(DISTINCT backend) at
                              FROM vault_objects WHERE kind IN ('db', 'manual', 'upgrade') AND deleted IS NULL
                              GROUP BY name ORDER BY MAX(id) DESC LIMIT ?""", (limit,)).fetchall()
        second = {r[0] for r in con.execute("SELECT name FROM vault_secondary")} if con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='vault_secondary'").fetchone() else set()
    finally:
        con.close()
    return [dict(name=r[0], created=r[1], size=r[2] or 0, at=r[3] or "", second=r[0] in second) for r in rows]

def check_backup(connect, master_secret):

    import shutil
    import tempfile
    from . import backup as BK, sync as SY
    found = backups(connect, 1)
    if not found:
        return [("", False, "No backup yet.")]
    name = found[0]["name"]
    places = [("Backups", store_backends()[0])]
    second = SY.second_backend()
    if second is not None and found[0]["second"]:
        places.append(("Second location", second))
    out = []
    for where, bk in places:
        tmp = tempfile.mkdtemp(prefix="crm-check-")
        try:
            c_ = connect()
            try:
                exp = BK.recorded_sha(c_, name, getattr(bk, "kind", None)) or BK.recorded_sha(c_, name)
            finally:
                c_.close()
            d = BK.restore(name, master_secret, os.path.join(tmp, "r"), backend=bk, expected_sha=exp)
            import datakey
            import db as D
            key = datakey.unwrap_master(master_secret, ring_path=os.path.join(d, "keyring.json"))
            con = D.connect(os.path.join(d, "crm.db"), key=key)
            try:
                n = con.execute("SELECT COUNT(*) FROM households").fetchone()[0]
            finally:
                con.close()
            out.append((where, True, f"Opened the backup from {found[0]['created'][:16].replace('T', ' ')}: {n} clients, intact."))
        except Exception as e:
            from . import seal as S_
            words = (str(e)[:1].upper() + str(e)[1:] + "." if isinstance(e, S_.Mismatch) else
                     "That isn't the master key for this install." if isinstance(e, ValueError) or "decrypt" in str(e).lower()
                     or "key" in type(e).__name__.lower() else f"Couldn't open it: {type(e).__name__}.")
            out.append((where, False, words))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return out

def register(app, connect, audit):
    @app.get("/vault", endpoint="vault_view")
    def vault_view():
        con = connect()
        try:
            K.init(con)
            mp = K.master_pub(con)
            objs = con.execute("""SELECT kind, backend, COUNT(*) n, COALESCE(SUM(size),0) b FROM vault_objects
                                  WHERE deleted IS NULL GROUP BY kind, backend""").fetchall()
            last_db = con.execute("SELECT MAX(created) FROM vault_objects WHERE kind IN ('db', 'manual', 'upgrade') AND deleted IS NULL").fetchone()[0]
            n_keys = con.execute("SELECT COUNT(*) FROM vault_keys").fetchone()[0]
        finally:
            con.close()
        c = config()
        con = connect()
        try:
            btime, blast = _setting(con, "backup_time", "02:40"), _setting(con, "backup_last_run")
        finally:
            con.close()
        from flask import session as _s
        import datakey as _dk
        ring_mp = _dk.master_pub()
        con = connect()
        try:
            second_err = _setting(con, "backup_second_error")
        finally:
            con.close()
        return render_template("vault.html", master=mp, master_mismatch=bool(mp and ring_mp and mp != ring_mp),
                               second_err=second_err, objs=objs, last_db=last_db, n_keys=n_keys, btime=btime, blast=blast,
                               backups=backups(connect), result=_s.pop("vault_result", None),
                               primary=describe(c.get("primary")), offsite=describe(c.get("secondary")),
                               gallery=describe(c.get("gallery_host")))

    @app.post("/vault/backup-now", endpoint="vault_backup_now")
    def vault_backup_now():
        from datetime import datetime
        from flask import session as _s
        from . import backup as BK
        if not PATHS:
            abort(503)
        import jobs

        def work(job):
            try:
                BK.sealed_backup(str(PATHS["db"]), str(PATHS["faces"]) if PATHS["faces"].exists() else None, kind="manual")
            except Exception as e:
                raise RuntimeError(f"Backup failed: {type(e).__name__}. Check the backup location.") from e
            _record_run(connect, datetime.now())
            audit("Backed up now")
            return "Backed up."
        jobs.start(app, "backup-now", "Backing up now", work)
        return redirect(url_for("vault_view"))

    @app.post("/vault/check", endpoint="vault_check")
    def vault_check():

        from flask import session as _s
        secret = (request.form.get("secret") or "").strip()
        if not secret.startswith("AGE-SECRET-KEY-1") or len(secret) > 200:
            _s["vault_result"] = [("", False, "Paste the whole master key (it starts AGE-SECRET-KEY-1).")]
        else:
            _s["vault_result"] = check_backup(connect, secret)
            audit("Checked the newest backup opens")
        return redirect(url_for("vault_view"))

    @app.post("/vault/schedule", endpoint="vault_schedule")
    def vault_schedule():
        import re as _re
        t = request.form.get("backup_time", "")
        if _re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            con = connect()
            try:
                con.execute("INSERT INTO settings (key, value) VALUES ('backup_time', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (t,))
                con.commit()
            finally:
                con.close()
        return redirect(url_for("vault_view"))

    @app.post("/vault/new", endpoint="vault_new")
    def vault_new():
        con = connect()
        try:
            if K.master_pub(con):
                abort(409)
        finally:
            con.close()
        now = time.time()
        PENDING.clear()
        secret, pub = K.generate_master()
        tok = secrets.token_urlsafe(16)
        PENDING[tok] = dict(secret=secret, pub=pub, at=now)
        return redirect(url_for("vault_ceremony", tok=tok))

    @app.get("/vault/new/<tok>", endpoint="vault_ceremony")
    def vault_ceremony(tok):
        p = PENDING.get(tok)
        if not p or time.time() - p["at"] > TTL:
            PENDING.pop(tok, None)
            return redirect(url_for("vault_view"))

        secret = p.pop("secret", None)
        if secret:
            p["tail"] = secret[-6:].upper()
        r = app.make_response(render_template("vault_ceremony.html", secret=secret, pub=p["pub"], tok=tok,
                                              err=request.args.get("err")))
        r.headers["Cache-Control"] = "no-store"

        r.headers["Referrer-Policy"] = "same-origin"
        return r

    @app.post("/vault/confirm/<tok>", endpoint="vault_confirm")
    def vault_confirm(tok):
        p = PENDING.get(tok)
        if not p or time.time() - p["at"] > TTL or "tail" not in p:
            PENDING.pop(tok, None)
            return redirect(url_for("vault_view"))
        if request.form.get("tail", "").strip().upper() != p["tail"]:
            return redirect(url_for("vault_ceremony", tok=tok, err=1))
        con = connect()
        try:
            K.init(con)
            K.set_master_pub(con, p["pub"])
        finally:
            con.close()
        PENDING.pop(tok, None)
        audit("Created the vault master key (public half stored; private half kept by the owner)")
        return redirect(url_for("vault_view"))
