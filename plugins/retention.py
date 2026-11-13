"""What a shoot leaves behind at home, and when it goes.

Per session: the RAWs and edited exports uploaded on the session page (sealed on arrival, photostore.py); with the
opt-in CRM_PHOTO_FOLDERS=1, also a plain RAW folder (<photos>/raw/YYYY/YYYY-MM-DD_<slug>/) picked from the
folders filed for the shoot date. A set number of days after delivery (SD card shipped, else the day it moved to
Delivered; the type's "keep the unedited files" number, 90 by default, the same number the agreement prints) the
daily pass deletes that folder, the export folder the gallery was built from, and the local encrypted gallery copy.
Today shows it a week ahead with "Keep another year". Deletion only ever touches a folder that sits exactly one or two
levels under the photo roots - never a root, never a year folder - and it is audited.
"""
import os
import shutil
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from flask import redirect, request, url_for

_DATA = Path(os.environ.get("CRM_DB", Path(__file__).resolve().parent.parent / "data" / "crm.db")).parent
PHOTOS = Path(os.environ.get("CRM_PHOTOS_ROOT", _DATA / "photos"))
GALLERIES = Path(os.environ.get("CRM_GALLERY_ROOT", _DATA / "galleries"))
KEEP_DAYS = 365          # "Keep another year"
FOLDERS = os.environ.get("CRM_PHOTO_FOLDERS", "") == "1"     # opt-in: pick plain RAW folders on a share (see docs)
WARN_DAYS = 7

BLOCK = """
{% if folders %}<form method="post" action="{{ url_for('retention_raw', sid=s.id) }}" class="row" style="flex-wrap:wrap;gap:6px">
  <input type="hidden" name="back" value="{{ back }}">
  {% if dirs %}<span class="seg">{% for d_ in dirs %}<label><input type="radio" name="raw_dir" value="{{ d_ }}" data-autosubmit {{ 'checked' if r and r.raw_dir == d_ }}><span>{{ d_.rstrip('/').split('/')[-1] }}</span></label>{% endfor %}</span>
  {% elif not r or not r.raw_dir %}<span class="mute" style="font-size:13px">No RAW folder filed for {{ nice(s.shoot_date) if s.shoot_date else 'this shoot' }}</span>{% endif %}
</form>{% endif %}
{% if r and r.sealed_at and not r.purged_at %}<div class="row" style="font-size:13px"><span class="grow">Sealed {{ nice(r.sealed_at) }}</span>
  {% if r.unsealed_dir %}<span class="mute" data-tip="Removed after {{ reprint_days }} days">Reprint copy: {{ r.unsealed_dir }}</span>
  {% else %}<details class="early" {{ 'open' if failed }}><summary>Open for a reprint</summary>{% if failed %}<div style="color:var(--crit);font-size:12.5px">That key did not open it.</div>{% endif %}<form method="post" action="{{ url_for('retention_unseal', sid=s.id) }}" class="row" style="margin-top:6px">
    <input type="hidden" name="back" value="{{ back }}"><input type="password" name="master" placeholder="Master key" autocomplete="off" required class="grow" style="width:auto"><input name="only" placeholder="Only these files (blank: all)" style="width:200px">
    <button class="quiet">Open</button></form></details>{% endif %}</div>
{% elif plain and master and not (r and r.purged_at) %}<div class="row" style="font-size:13px">{% if sdate %}<span class="grow" data-tip="RAWs and exports are encrypted and the plaintext removed">Seals {{ nice(sdate.isoformat()) }}</span>{% endif %}<form method="post" action="{{ url_for('retention_seal', sid=s.id) }}" class="inline"><input type="hidden" name="back" value="{{ back }}"><button class="quiet">Seal now</button></form></div>{% endif %}
{% if r and r.purged_at %}<div class="sub" style="font-size:13px">RAWs and outtakes deleted {{ nice(r.purged_at) }}{% if r.early_note %} ({{ r.early_note }}){% endif %}</div>
{% elif due %}<div class="row" style="font-size:13px"><span class="grow" {% if soon %}style="color:var(--warn)"{% endif %}>RAWs and outtakes delete {{ nice(due.isoformat()) }}</span>
  <form method="post" action="{{ url_for('retention_keep', sid=s.id) }}" class="inline"><input type="hidden" name="back" value="{{ back }}"><button class="quiet">Keep another year</button></form></div>{% endif %}
{% if not (r and r.purged_at) %}<details class="early"><summary>Delete early</summary>
  <form method="post" action="{{ url_for('retention_early', sid=s.id) }}" class="row" style="flex-wrap:wrap;gap:6px;margin-top:6px"
        data-confirm="Delete this client's RAWs, exports and gallery now, at home and on the web? This cannot be undone.">
    <input type="hidden" name="back" value="{{ back }}">
    <input type="date" name="asked_on" required style="width:auto" title="Date of the written request">
    <span class="seg">{% for w in ['email', 'letter', 'text'] %}<label><input type="radio" name="asked_by" value="{{ w }}" required{{ ' checked' if loop.first }}><span>{{ w }}</span></label>{% endfor %}</span>
    <button class="danger">Delete now</button></form></details>{% endif %}
"""


def _safe(path, root, depth):
    """True only for a real directory exactly `depth` levels under root (raw/YYYY/<shoot> = 2, export/<set> = 1)."""
    try:
        p = Path(path).resolve()
        rr = root.resolve()
    except (OSError, RuntimeError):
        return False
    return p.is_dir() and rr in p.parents and len(p.relative_to(rr).parts) == depth


def register(ctx):
    app, q, x, C = ctx.app, ctx.q, ctx.x, ctx.C
    from flask import render_template_string
    ctx.migrate("""CREATE TABLE IF NOT EXISTS retention (
        session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        raw_dir TEXT DEFAULT '', keep_until TEXT, purged_at TEXT);""")
    @ctx.on_unlock
    def _migrate_cols():      # the database opens only after sign-in
        _con = ctx.connect()
        _cols = [r_[1] for r_ in _con.execute("PRAGMA table_info(retention)")]
        _upgrading = "keep_days" not in _cols and bool(_con.execute("SELECT 1 FROM sessions LIMIT 1").fetchone())
        for _c, _d in (("early_note", "TEXT DEFAULT ''"), ("sealed_at", "TEXT"), ("unsealed_dir", "TEXT DEFAULT ''"), ("unsealed_at", "TEXT"),
                       ("notice", "TEXT DEFAULT '{}'"), ("keep_days", "INTEGER")):
            if _c not in _cols:
                ctx.add_column(_con, "retention", _c, _d)
        if _upgrading:
            # an install from before per-type keeping: every session that already exists keeps what its agreement said
            # then (a year, or the type's own words), never the new 90-day default
            import config as _C
            import json as _json
            try:                                # the photographer's own types (settings are applied after this runs)
                _types = _json.loads((_con.execute("SELECT value FROM settings WHERE key='rules'").fetchone() or ["{}"])[0] or "{}").get("TYPES") or []
            except ValueError:
                _types = []
            # only sessions already agreed: an inquiry or quote not yet signed will sign today's wording
            for _s in _con.execute("""SELECT id, type FROM sessions WHERE stage IN ('booked','shot','delivered','closed')
                                      OR id IN (SELECT session_id FROM documents WHERE kind='agreement' AND status='signed')""").fetchall():
                _own = next(((t.get("policy") or {}) for t in _types if t.get("key") == _s[1]), {})
                _days = _C.phrase_days(_own.get("keep_raw")) or 365
                _con.execute("""INSERT INTO retention (session_id, keep_days) VALUES (?,?)
                                ON CONFLICT(session_id) DO UPDATE SET keep_days=COALESCE(retention.keep_days, excluded.keep_days)""",
                             (_s[0], _days))
        _con.commit()
        _con.close()

    def row(sid):
        return q("SELECT * FROM retention WHERE session_id=?", (sid,), one=True)

    def delivered_on(s):
        if s["sd_shipped"]:
            return s["sd_shipped"][:10]
        r = q("SELECT MIN(at) a FROM stage_log WHERE session_id=? AND stage='delivered'", (s["id"],), one=True)
        return (r["a"] or "")[:10] or None

    def keep_days(s, r):
        """The days this session's unedited files are kept: fixed once it is delivered (a later change to the type's
        number applies to later sessions, not to files already on their clock)."""
        if r and "keep_days" in r.keys() and r["keep_days"]:
            return int(r["keep_days"])
        return ctx.C.raw_keep_days(s["type"])

    def due_date(s, r):
        if r and r["keep_until"]:
            return date.fromisoformat(r["keep_until"])
        dv = delivered_on(s) if s["stage"] in ("delivered", "closed") else None
        return date.fromisoformat(dv) + timedelta(days=keep_days(s, r)) if dv else None

    def uploads(sid):
        import photostore
        con = ctx.connect()
        try:
            return photostore.files(con, sid, done_only=True)
        finally:
            con.close()

    def busy_uploading(sid):
        import photostore
        con = ctx.connect()
        try:
            return bool(con.execute("SELECT 1 FROM photo_files WHERE session_id=? AND state='partial'", (sid,)).fetchone())
        finally:
            con.close()

    def raw_dirs(s):
        if not s["shoot_date"] or not FOLDERS:
            return []
        base = PHOTOS / "raw" / s["shoot_date"][:4]
        try:
            return sorted(str(d_) + "/" for d_ in base.iterdir() if d_.is_dir() and d_.name.startswith(s["shoot_date"]))
        except OSError:
            return []

    def back_of(s):
        return url_for("client_stage", hid=s["household_id"]) + f"?s={s['id']}"

    @app.post("/session/<int:sid>/raw", endpoint="retention_raw")
    def retention_raw(sid):
        s = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        d_ = request.form.get("raw_dir", "")
        if s and d_ in raw_dirs(s):
            x("""INSERT INTO retention (session_id, raw_dir) VALUES (?,?)
                 ON CONFLICT(session_id) DO UPDATE SET raw_dir=excluded.raw_dir""", (sid, d_))
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("home"))

    @app.post("/session/<int:sid>/raw/early", endpoint="retention_early")
    def retention_early(sid):
        """at the client's WRITTEN request, everything of theirs can go early. The request (date and
        how it came) is required and recorded; the web gallery comes down too."""
        s = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        asked_on, asked_by = request.form.get("asked_on", ""), request.form.get("asked_by", "")
        try:
            asked_on = date.fromisoformat(asked_on).isoformat() if asked_on else ""
        except ValueError:
            asked_on = ""                                   # checked before anything is deleted
        if not s or not asked_on or asked_by not in ("email", "letter", "text"):
            return redirect(ctx.safe_back(request.form.get("back")) or url_for("home"))
        note = f"early, at the client's written request by {asked_by}, {ctx.nice(asked_on)}"

        def work(job):                                  # a background job: galleries over the network, then the purge
            if app.config.get("gallery_take_down"):
                try:
                    app.config["gallery_take_down"](sid)
                except Exception:
                    app.logger.exception("early take-down %s", sid)
            purge(s, row(sid))
            x("UPDATE retention SET early_note=? WHERE session_id=?", (note, sid))
            ctx.audit("Deleted photos " + note, sid=sid, who="Owner")
            return "Photos deleted, at the client's request."
        import jobs
        jobs.start(app, f"purge:{sid}", "Deleting photos at the client's request", work)
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("home"))

    @app.post("/session/<int:sid>/seal", endpoint="retention_seal")
    def retention_seal(sid):
        s = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        if s and has_master():
            import jobs

            def work(job):
                return "Sealed into the vault." if seal(s, row(sid)) else "Nothing to seal (or an upload is still unfinished)."
            jobs.start(app, f"seal:{sid}", "Sealing photos into the vault", work)
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("home"))

    @app.post("/session/<int:sid>/unseal", endpoint="retention_unseal")
    def retention_unseal(sid):
        """Open a sealed session for a reprint: the master key is typed in, used once, and not kept."""
        import secrets as _secrets
        VS, VW, _ = vault()
        secret = request.form.get("master", "")
        dest = UNSEALED / _secrets.token_hex(8)
        only = [w_.strip() for w_ in (request.form.get("only") or "").split(",") if w_.strip()]
        con = ctx.connect()
        try:                                             # the key is checked at once, before any long work
            if str(VS.keys.parse_secret(secret).to_public()) != VS.keys.master_pub(con):
                raise ValueError("not this install's master key")
        except Exception:
            app.logger.warning("unseal %s refused", sid)
            back = ctx.safe_back(request.form.get("back")) or url_for("home")
            return redirect(back + ("&" if "?" in back else "?") + "unseal=failed")
        finally:
            con.close()

        def work(job):                                   # the download and decryption run as a job
            con_ = ctx.connect()
            try:
                import photostore
                n_ = photostore.open_sealed(con_, sid, secret, VW.store_backends()[0], str(dest), only)   # one file at a time
                if con_.execute("SELECT 1 FROM vault_objects WHERE session_id=? AND kind='session' AND deleted IS NULL",
                                (sid,)).fetchone():
                    VS.open_session(con_, sid, secret, VW.store_backends()[0], str(dest))          # an opt-in folder archive
                job["message"] = f"{n_} photo{'s' if n_ != 1 else ''} opened."
            finally:
                con_.close()
            x("""INSERT INTO retention (session_id, unsealed_dir, unsealed_at) VALUES (?,?,?)
                 ON CONFLICT(session_id) DO UPDATE SET unsealed_dir=excluded.unsealed_dir, unsealed_at=excluded.unsealed_at""",
              (sid, str(dest), date.today().isoformat()))
            ctx.audit(f"Opened the sealed photos for a reprint (copy removed after {REPRINT_DAYS} days)", sid=sid, who="Owner")
            return job.get("message") or "Opened for a reprint."
        import jobs
        jobs.start(app, f"unseal:{sid}", "Opening sealed photos for a reprint", work)
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("home"))

    @app.post("/session/<int:sid>/raw/keep", endpoint="retention_keep")
    def retention_keep(sid):
        s = q("SELECT * FROM sessions WHERE id=?", (sid,), one=True)
        base = due_date(s, row(sid)) or date.today()
        until = (max(base, date.today()) + timedelta(days=KEEP_DAYS)).isoformat()
        x("""INSERT INTO retention (session_id, keep_until) VALUES (?,?)
             ON CONFLICT(session_id) DO UPDATE SET keep_until=excluded.keep_until""", (sid, until))
        return redirect(ctx.safe_back(request.form.get("back")) or url_for("home"))

    UNSEALED = PHOTOS / "unsealed"
    REPRINT_DAYS = 7
    SEAL_AFTER_DAYS = 7      # auto-seal a week after the SD card ships; Today warns before

    def seal_date(s):
        return date.fromisoformat(s["sd_shipped"][:10]) + timedelta(days=SEAL_AFTER_DAYS) if s["sd_shipped"] else None

    def vault():
        from vault import seal as VS, web as VW, keys as VK
        return VS, VW, VK

    def has_master():
        con = ctx.connect()
        try:
            return bool(vault()[2].master_pub(con))
        except Exception:
            return False
        finally:
            con.close()

    def plain_folders(s, r):
        out = []
        if r and r["raw_dir"] and _safe(r["raw_dir"], PHOTOS / "raw", 2):
            out.append(r["raw_dir"])
        g = q("SELECT source_dir FROM galleries WHERE session_id=?", (s["id"],), one=True) \
            if q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True) else None
        if g and g["source_dir"] and _safe(g["source_dir"], PHOTOS / "export", 1):
            out.append(g["source_dir"])
        return out

    def shared_with_others(s, folders):
        """A folder another live session also points at is never sealed away from under it."""
        others = []
        for f_ in folders:
            hit = q("""SELECT session_id FROM retention WHERE raw_dir=? AND session_id != ? AND purged_at IS NULL
                       AND sealed_at IS NULL""", (f_, s["id"]))
            if q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True):
                hit += q("SELECT session_id FROM galleries WHERE source_dir=? AND session_id != ?", (f_, s["id"]))
            others += [h["session_id"] for h in hit]
        return others

    def seal(s, r):
        """Seal the session's RAWs and exports under its own key, verify every copy, then remove exactly what was
        sealed (storage design: the CRM can seal but never open; the owner's master key opens). One at a time per
        session; never while an upload into it is unfinished (the next pass tries again)."""
        import locks
        lk = locks.session(s["id"])
        if not lk.acquire(blocking=False):
            return False                              # already sealing, deleting or building a gallery for this session
        locks.SEALING.add(s["id"])
        try:
            return _seal(s, row(s["id"]))             # re-read inside the lock: a seal that just finished is not redone
        finally:
            locks.SEALING.discard(s["id"])
            lk.release()

    def _seal(s, r):
        if r and r["sealed_at"]:
            return False
        if busy_uploading(s["id"]):
            app.logger.info("seal %s waits: an upload into it is unfinished", s["id"])
            return False
        VS, VW, _ = vault()
        folders = plain_folders(s, r)
        sealed_rows = uploads(s["id"])
        if not folders and not sealed_rows:
            return False
        if shared_with_others(s, folders):
            raise ValueError("a folder of this session is also used by another session; seal refused")
        con = ctx.connect()
        try:
            if folders:                                     # opt-in plain folders: one sealed archive, as before
                VS.seal_session(con, s["id"], folders, VW.store_backends())
            if sealed_rows:                                 # uploads: the same encrypted files go into the vault as they are
                import photostore
                from vault import keys as VK
                photostore.seal_to_vault(con, sealed_rows, VW.store_backends(), VK.master_pub(con),
                                         f"{s['household'] if 'household' in s.keys() else ''}".strip())
        finally:
            con.close()
        # record the seal BEFORE removing anything, so a half-finished removal is never sealed again as a 2nd archive
        x("""INSERT INTO retention (session_id, sealed_at, raw_dir) VALUES (?,?, '')
             ON CONFLICT(session_id) DO UPDATE SET sealed_at=excluded.sealed_at, raw_dir=''""", (s["id"], date.today().isoformat()))
        if q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True):
            x("UPDATE galleries SET source_dir='' WHERE session_id=?", (s["id"],))
        for f_ in folders:
            shutil.rmtree(f_, ignore_errors=True)
        left = [f_ for f_ in folders if os.path.exists(f_)]
        if left:
            app.logger.warning("sealed %s but could not remove %s; it stays until removed by hand", s["id"], left)
        ctx.audit("Sealed the RAWs and exports (encrypted; plaintext removed)", sid=s["id"], who="Lumen Aureum")
        return True

    def purge(s, r):
        """Delete everything of a session's photos, once. Takes the session's turn (waits for a seal or a gallery build
        to finish) so nothing it removes is being written at the same time."""
        import locks
        lk = locks.session(s["id"])
        if not lk.acquire(timeout=600):
            raise RuntimeError("the session's photos are busy (a seal or gallery build); purge tried again later")
        locks.SEALING.add(s["id"])
        try:
            return _purge(s, row(s["id"]))
        finally:
            locks.SEALING.discard(s["id"])
            lk.release()

    def _purge(s, r):
        gone = []
        con = ctx.connect()
        try:
            if con.execute("SELECT 1 FROM vault_keys WHERE session_id=?", (s["id"],)).fetchone() or con.execute(
                    "SELECT 1 FROM vault_objects WHERE session_id=? AND deleted IS NULL", (s["id"],)).fetchone():
                VS, VW, _ = vault()
                VS.destroy_session(con, s["id"], VW.store_backends())     # archives deleted, key shredded
                gone.append("sealed archive and its key")
        finally:
            con.close()
        if r and r["unsealed_dir"] and _safe(r["unsealed_dir"], UNSEALED, 1):
            shutil.rmtree(r["unsealed_dir"]); gone.append("reprint copy")
        import photostore                                # uploaded photos, finished or not
        con = ctx.connect()
        try:
            n_up = len(photostore.files(con, s["id"]))
            photostore.delete_session(con, s["id"])
        finally:
            con.close()
        if n_up:
            gone.append(f"{n_up} uploaded photo{'s' if n_up != 1 else ''}")
        if r and r["raw_dir"] and _safe(r["raw_dir"], PHOTOS / "raw", 2):
            shutil.rmtree(r["raw_dir"]); gone.append("RAWs")
        g = q("SELECT source_dir FROM galleries WHERE session_id=?", (s["id"],), one=True) \
            if q("SELECT 1 FROM sqlite_master WHERE name='galleries'", one=True) else None
        if g and g["source_dir"] and _safe(g["source_dir"], PHOTOS / "export", 1):
            shutil.rmtree(g["source_dir"]); gone.append("exports")
        if s["gallery_id"] and _safe(GALLERIES / s["gallery_id"], GALLERIES, 1):
            shutil.rmtree(GALLERIES / s["gallery_id"]); gone.append("local gallery")
        x("""INSERT INTO retention (session_id, purged_at) VALUES (?,?)
             ON CONFLICT(session_id) DO UPDATE SET purged_at=excluded.purged_at""", (s["id"], date.today().isoformat()))
        ctx.audit(f"Deleted {ctx.C.days_words(keep_days(s, r))} after delivery: " + (", ".join(gone) or "nothing on disk"), sid=s["id"], who="Lumen Aureum")

    SEAL_NOTICE_DAYS = 3      # an automatic seal waits until Today has shown it coming for this long
    PURGE_NOTICE_DAYS = 7     # and an automatic deletion for this long (a mistyped date shows up as a warning first)

    def noticed(sid, kind, target, days):
        """True once `kind` (seal or purge) for this target date has been on notice for `days`. The first time a
        target is seen, its notice starts today, however long ago the target itself was (a typo, a clock jump)."""
        import json
        r = row(sid)
        try:
            n = json.loads((r["notice"] if r and "notice" in r.keys() else "") or "{}")
        except ValueError:
            n = {}
        cur = n.get(kind)
        if not cur or cur[1] != target.isoformat():
            n[kind] = [date.today().isoformat(), target.isoformat()]
            x("""INSERT INTO retention (session_id, notice) VALUES (?,?)
                 ON CONFLICT(session_id) DO UPDATE SET notice=excluded.notice""", (sid, json.dumps(n)))
            return days <= 0
        return (date.today() - date.fromisoformat(cur[0])).days >= days

    def pass_once():
        if not app.config["clock_ok"]():
            app.logger.warning("retention pass skipped: the date jumped; waiting for the owner to confirm it on Today")
            return
        master = has_master()
        for r0 in q("SELECT * FROM retention WHERE unsealed_dir != '' AND unsealed_at IS NOT NULL"):
            if date.today() > date.fromisoformat(r0["unsealed_at"][:10]) + timedelta(days=REPRINT_DAYS):
                if _safe(r0["unsealed_dir"], UNSEALED, 1):
                    shutil.rmtree(r0["unsealed_dir"], ignore_errors=True)
                x("UPDATE retention SET unsealed_dir='', unsealed_at=NULL WHERE session_id=?", (r0["session_id"],))
        for s in q("SELECT * FROM sessions WHERE stage IN ('delivered','closed')"):
            r = row(s["id"])
            if r and r["purged_at"]:
                continue
            if r and r["unsealed_dir"] and r["unsealed_at"] and \
                    date.today() > date.fromisoformat(r["unsealed_at"][:10]) + timedelta(days=REPRINT_DAYS):
                if _safe(r["unsealed_dir"], UNSEALED, 1):
                    shutil.rmtree(r["unsealed_dir"])
                x("UPDATE retention SET unsealed_dir='', unsealed_at=NULL WHERE session_id=?", (s["id"],))
            sd_ = seal_date(s)
            if master and sd_ and date.today() >= sd_ - timedelta(days=SEAL_NOTICE_DAYS) and not (r and r["sealed_at"]) \
                    and (plain_folders(s, r) or uploads(s["id"])):
                if noticed(s["id"], "seal", sd_, SEAL_NOTICE_DAYS) and date.today() >= sd_:
                    try:
                        seal(s, r)               # delivered of record, and shown on Today first: seal, drop the plaintext
                    except Exception:
                        app.logger.exception("retention seal %s", s["id"])
                r = row(s["id"])
            if not (r and r["keep_days"]) and delivered_on(s):
                x("""INSERT INTO retention (session_id, keep_days) VALUES (?,?)
                     ON CONFLICT(session_id) DO UPDATE SET keep_days=excluded.keep_days""", (s["id"], ctx.C.raw_keep_days(s["type"])))
                r = row(s["id"])
            due = due_date(s, r)
            if due and date.today() >= due - timedelta(days=PURGE_NOTICE_DAYS):
                if noticed(s["id"], "purge", due, PURGE_NOTICE_DAYS) and date.today() > due:
                    try:
                        purge(s, r)
                    except Exception:
                        app.logger.exception("retention purge %s", s["id"])

    def loop():
        time.sleep(60)
        while True:
            if not ctx.unlocked():          # locked after a restart: wait for the owner to sign in
                time.sleep(60)
                continue
            try:
                with app.app_context():
                    pass_once()
            except Exception:
                app.logger.exception("retention loop")
            time.sleep(6 * 3600)

    if not os.environ.get("CRM_TESTING"):      # tests never start background jobs
        threading.Thread(target=loop, name="retention", daemon=True).start()
    app.config["retention_pass"] = pass_once
    app.config["retention_purge_now"] = lambda sid: purge(q("SELECT * FROM sessions WHERE id=?", (sid,), one=True), row(sid))

    def block(s):
        if s["stage"] not in ("shot", "delivered", "closed"):
            return []
        r = row(s["id"])
        due = due_date(s, r)
        soon = bool(due and (due - date.today()).days <= WARN_DAYS)
        html = render_template_string(BLOCK, s=s, r=r, dirs=raw_dirs(s) if not (r and r["sealed_at"]) else [], due=due, soon=soon,
                                      nice=ctx.nice, back=back_of(s), plain=bool(plain_folders(s, r) or uploads(s["id"])), master=has_master(),
                                      reprint_days=REPRINT_DAYS, sdate=seal_date(s), folders=FOLDERS,
                                      failed=request.args.get("unseal") == "failed")
        return [("Photo storage", html)]

    ctx.hooks.stage_blocks.append(block)

    def today():
        """Today's warnings: what seals or deletes soon. Read in a few queries for the whole studio (not per session)."""
        out = []
        rets = {r_["session_id"]: r_ for r_ in q("SELECT * FROM retention")}
        delivered = {r_[0]: r_[1] for r_ in q("SELECT session_id, MIN(at) FROM stage_log WHERE stage='delivered' GROUP BY session_id")}
        import photostore
        with_uploads = {r_[0] for r_ in q("SELECT DISTINCT session_id FROM photo_files WHERE state='done'")} \
            if q("SELECT 1 FROM sqlite_master WHERE name='photo_files'", one=True) else set()
        master = None
        for s in q("""SELECT s.*, h.name AS household FROM sessions s JOIN households h ON h.id=s.household_id
                      WHERE s.stage IN ('delivered','closed')"""):
            r = rets.get(s["id"])
            if r and r["purged_at"]:
                continue
            if r and r["keep_until"]:
                due = date.fromisoformat(r["keep_until"])
            else:
                dv = s["sd_shipped"][:10] if s["sd_shipped"] else (delivered.get(s["id"]) or "")[:10]
                due = date.fromisoformat(dv) + timedelta(days=keep_days(s, r)) if dv else None
            sd_ = seal_date(s)
            if sd_ and not (r and r["sealed_at"]) and (sd_ - date.today()).days <= 3 and (s["id"] in with_uploads or plain_folders(s, r)):
                if master is None:
                    master = has_master()
                if master:
                    out.append(dict(text="RAWs and exports seal", when=sd_.isoformat(), link=back_of(s),
                                    who=dict(name=s["household"], sub=ctx.C.SESSION_TYPES.get(s["type"], s["type"]),
                                             link=back_of(s), type=s["type"], hid=s["household_id"])))
            if due and (due - date.today()).days <= WARN_DAYS:
                out.append(dict(text="RAWs and outtakes delete", when=due.isoformat(), link=back_of(s),
                                who=dict(name=s["household"], sub=ctx.C.SESSION_TYPES.get(s["type"], s["type"]),
                                         link=back_of(s), type=s["type"], hid=s["household_id"])))
        return out

    ctx.hooks.today.append(today)
