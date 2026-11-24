import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import date, datetime

import errtext
import hostcheck
import items
import legacy_130
import refit
import sealed
import webtls

KEY = "upgrade_140"
LEGACY = {"picks": "picks.bin", "draft": "draft.bin"}
NEW = {"picks": "picks.las", "draft": "draft.las"}
SENT = ("picks.bin", "draft.bin", "picks.las", "draft.las")
LOCK = threading.RLock()

NOT_COPIED = {"index.html", "tries.php"} | set(SENT)

def sha(b):
    return hashlib.sha256(b).hexdigest() if b is not None else ""

def load(q):
    return refit.load_state(q, KEY)

def save(x, st):
    refit.save_state(x, st, KEY)

def finished(q):
    try:
        return bool(load(q).get("finished"))
    except Exception:
        return False

def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)

def refit_at(at):
    from datetime import timezone
    try:
        d = datetime.fromisoformat(str(at or "").replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, OverflowError, OSError):
        return 0.0

def at_of(payload):
    try:
        return refit_at(json.loads(payload).get("at"))
    except Exception:
        return 0.0

def with_item(payload):

    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if items.item_of(data):
        return bytes(payload)
    iid = sha(bytes(payload))[:32]
    body = bytes(payload).strip()
    out = b'{"id":"' + iid.encode() + b'",' + body[1:] if body.startswith(b"{") and body != b"{}" else b""
    try:
        if out and json.loads(out) == dict(data, id=iid):
            return out
    except ValueError:
        pass
    return json.dumps(dict(data, id=iid), separators=(",", ":")).encode()

def is_link(G, gid):
    return bool(getattr(G, "is_link", None)) and G.is_link(gid)

def _open_new(G, gid, key, purpose, blob):

    try:
        return sealed.open_link(purpose, gid, G.key_bytes(key), blob)
    except Exception:
        return None

def _open_any(G, folder, key, purpose, name, blob):

    if blob is None:
        return None
    if name == LEGACY[purpose]:
        try:
            return legacy_130.open_picks_payload(blob, key, name)
        except ValueError:
            return None
    if is_link(G, folder):
        return _open_new(G, folder, key, purpose, blob)
    try:
        return legacy_130.open_las_before_links(purpose, folder, G.key_bytes(key), blob)
    except ValueError:
        return None

def open_order_before_links(private_keys, env, gid, key):

    return legacy_130.open_order_before_links(private_keys, env, gid, key)

def online_rows(q, where=""):

    return q("""SELECT g.*, s.gallery_id, s.id AS sid, s.first_visit, s.retouch_picks, s.favorites, s.veto, s.household_id
                FROM galleries g JOIN sessions s ON s.id=g.session_id
                WHERE g.removed_at IS NULL AND (g.upload_state='up' OR (g.upload_state='error' AND g.uploaded_at IS NOT NULL))
                """ + where)

ROW = """SELECT g.*, s.gallery_id, s.id AS sid, s.first_visit, s.retouch_picks, s.favorites, s.veto, s.household_id
         FROM galleries g JOIN sessions s ON s.id=g.session_id"""

def _row_sid(q, sid):
    return q(ROW + " WHERE s.id=?", (sid,), one=True)

def _row_folder(q, folder):

    return q(ROW + " WHERE s.gallery_id=? OR g.link_was=? ORDER BY s.gallery_id=? DESC", (folder, folder, folder), one=True)

class Env:

    def __init__(self, ctx, G, studio=None):
        self.ctx, self.G, self.studio = ctx, G, studio
        self.q, self.x = ctx.q, ctx.x
        self.st = load(self.q)
        self.notes = []

    def save(self):
        save(self.x, self.st)

    def note(self, t):
        self.notes.append(t)

    def record_picks(self, r, payload, blob_sha):

        G = self.G
        try:
            data = json.loads(payload)
            pk = G._shape(data)
        except Exception:
            return
        gid = r["gallery_id"]
        iid = items.item_of(data)
        if iid and items.state(self.q, "picks", gid, iid) == "older":
            return
        if blob_sha:
            self.x("UPDATE galleries SET picks_hash=? WHERE session_id=?", (blob_sha, r["sid"]))
        if iid and is_link(G, gid):
            items.take(self.q, self.x, "picks", gid, iid)
        want = ("\n".join(pk["retouch"]), ", ".join(pk["suggest"]), ", ".join(pk["veto"]))
        cur = self.q("SELECT retouch_picks, favorites, veto FROM sessions WHERE id=?", (r["sid"],), one=True)
        if cur is not None and ((cur["retouch_picks"] or ""), (cur["favorites"] or ""), (cur["veto"] or "")) == want:
            return
        self.x("UPDATE sessions SET retouch_picks=?, favorites=?, veto=? WHERE id=?", want + (r["sid"],))
        self.ctx.audit(f"Picks received from the gallery: {len(pk['retouch'])} to retouch, "
                       f"{len(pk['suggest'])} suggested for the portfolio, {len(pk['veto'])} not to show",
                       sid=r["sid"], who="Lumen Aureum")
        self.ctx.advance(r["sid"])

    def draft_status(self, sid):
        fn = self.ctx.app.config.get("gallery_draft_status_sid")
        if fn:
            try:
                fn(sid)
            except Exception:
                self.ctx.app.logger.exception("upgrade 1.4: draft status %s", sid)

    def base_for(self, r, hfp):

        cur = self.G.host_fingerprint() if self.G.has_host() else ""
        b = self.q("SELECT value FROM settings WHERE key='gallery_base_url'", one=True)
        now = (b["value"] if b and b["value"] else "").strip()
        if hfp == cur:
            return now
        return (r["link_base"] if "link_base" in r.keys() and r["link_base"] else "") or now

def take_in(env, r, host, folder, put_host=None):

    G, gid, key = env.G, r["gallery_id"], r["ekey"] or r["key"]
    moved = is_link(G, gid)
    kept = G.KEPT_ROOT / gid
    read = {}
    for purpose in ("picks", "draft"):
        found = []
        for name in (LEGACY[purpose], NEW[purpose]):
            blob = G._read(host, f"{folder}/{name}")
            if blob is None:
                continue
            read[name] = sha(blob)
            pl = _open_any(G, folder, key, purpose, name, blob)
            if pl is None:
                read[name + "?"] = sha(blob)
                continue
            found.append((at_of(pl), 3, pl, blob, "folder:" + name))
        if not moved:
            cands = found + _kept_cands(G, kept, gid, key, purpose)
            if not cands:
                continue
            best = max(cands, key=lambda c: (c[0], c[1]))
            if best[4].startswith("folder:"):
                name = best[4].split(":", 1)[1]
                if not (kept / name).is_file() or (kept / name).read_bytes() != best[3]:
                    _write(kept / name, best[3])
            if purpose == "picks":
                env.record_picks(r, best[2], "")
            continue
        theirs = G._read(put_host, f"{gid}/{NEW[purpose]}") if put_host is not None else None
        for _try in range(3):
            got = _carry(env, G, gid, key, purpose, kept, found, theirs, put_host is not None)
            if got is None:
                break
            want, mine = got
            if put_host is not None and theirs != mine:

                now = G._read(put_host, f"{gid}/{NEW[purpose]}")
                if now != theirs:
                    theirs = now
                    continue
                put_host.put_stream(f"{gid}/{NEW[purpose]}", io.BytesIO(mine), content_type="application/octet-stream")
                if G._read(put_host, f"{gid}/{NEW[purpose]}") != mine:
                    raise refit.Wait("readback", "a carried-over file read back differently")
            if purpose == "picks":
                env.record_picks(r, want, sha(mine))
            break
        else:
            raise refit.Wait("busy", "the client is sending to the gallery right now")
    return read

def _kept_cands(G, kept, gid, key, purpose):
    out = []
    for name in (LEGACY[purpose], NEW[purpose]):
        p = kept / name
        if p.is_file():
            blob = p.read_bytes()
            pl = _open_any(G, gid, key, purpose, name, blob)
            if pl is not None:
                out.append((at_of(pl), 2 if name == NEW[purpose] else 1, pl, blob, "kept:" + name))
    return out

def _carry(env, G, gid, key, purpose, kept, found, theirs, to_host):

    cands = found + _kept_cands(G, kept, gid, key, purpose)
    if to_host and theirs is not None:
        pl = _open_new(G, gid, key, purpose, theirs)
        if pl is not None:
            cands.append((at_of(pl), 4, pl, theirs, "new"))
    if not cands:
        return None
    best = max(cands, key=lambda c: (c[0], c[1]))
    p = kept / NEW[purpose]
    if best[4] == "new" or best[4] == "kept:" + NEW[purpose]:
        want = best[2]
        if not p.is_file() or p.read_bytes() != best[3]:
            _write(p, best[3])
    else:
        want = with_item(best[2])
        if want is None:
            return None
        have = _open_new(G, gid, key, purpose, p.read_bytes()) if p.is_file() else None
        if have != want:
            env_ = sealed.seal_link(purpose, gid, G.key_bytes(key), want)
            if sealed.open_link(purpose, gid, G.key_bytes(key), env_) != want:
                raise RuntimeError("a carried-over envelope did not open to its payload")
            _write(p, env_)
            if p.read_bytes() != env_:
                raise RuntimeError("a carried-over envelope did not read back")
    return want, p.read_bytes()

def read_waiting(ctx, G, r, host):

    if not LOCK.acquire(blocking=False):
        return
    try:
        env = Env(ctx, G)
        env.save = lambda: None
        take_in(env, r, host, r["gallery_id"])
    finally:
        LOCK.release()

def read_old(ctx, G, r, host):

    if not r["link_was"] or not LOCK.acquire(blocking=False):
        return
    try:
        env = Env(ctx, G)
        env.save = lambda: None
        take_in(env, r, host, r["link_was"], put_host=host)
    finally:
        LOCK.release()

class _Keep:
    pass

def collect_before_delete(ctx, G, folder, host, con):

    def q_(sql, args=(), one=False):
        rows = con.execute(sql, args).fetchall()
        return (rows[0] if rows else None) if one else rows

    def x_(sql, args=()):
        con.execute(sql, args)
        con.commit()

    def audit_(action, sid=None, who="Lumen Aureum", **_k):
        h = q_("SELECT household_id FROM sessions WHERE id=?", (sid,), one=True) if sid else None
        x_("INSERT INTO audit (at, household_id, session_id, action, who) VALUES (?,?,?,?,?)",
           (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), h["household_id"] if h else None, sid, action, who))
    if not G.GID_OK.match(folder or ""):
        return
    r = _row_folder(q_, folder)
    if r is None:
        return
    if folder == r["gallery_id"] and is_link(G, folder):
        return
    if not any(host.stat(f"{folder}/{n}") is not None for n in SENT):
        return
    c = _Keep()
    c.app, c.q, c.x, c.audit, c.advance, c.hooks = ctx.app, q_, x_, audit_, (lambda sid: None), getattr(ctx, "hooks", None)
    with LOCK:
        env = Env(c, G)
        env.save = lambda: None
        take_in(env, r, host, folder)

def link_for(env, sid):

    links = env.st.setdefault("links", {})
    n = links.get(str(sid))
    if not (isinstance(n, str) and sealed.LINK_ID.fullmatch(n) and n[0] == "g"):
        n = links[str(sid)] = sealed.link_id("g")
        env.save()
    return n

def _commit(ctx, stmts):

    import locks
    from flask import g as _g, has_app_context
    own = not (has_app_context() and "db" in _g)
    if not locks.WRITES.acquire(timeout=120):
        raise refit.Wait("busy", "the app is busy; tried again at the next check")
    try:
        con = ctx.connect() if own else _g.db
        if not own:
            con.commit()
        try:
            con.execute("BEGIN IMMEDIATE")
            for sql, a in stmts:
                try:
                    con.execute(sql, a)
                except Exception as e:
                    if "no such table" not in str(e):
                        raise
            con.execute("COMMIT")
        except BaseException:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            if own:
                con.close()
    finally:
        locks.WRITES.release()

def relink(env, r, was_fp=None, was_open=False, audit=True):

    G = env.G
    old, sid, key = r["gallery_id"], r["sid"], r["ekey"] or r["key"]
    if is_link(G, old):
        return old
    new = link_for(env, sid)
    for purpose in ("picks", "draft"):
        cands = []
        p_new = G.KEPT_ROOT / new / NEW[purpose]
        for name in (LEGACY[purpose], NEW[purpose]):
            p = G.KEPT_ROOT / old / name
            if p.is_file():
                pl = _open_any(G, old, key, purpose, name, p.read_bytes())
                if pl is not None:
                    cands.append((at_of(pl), 1 if name == NEW[purpose] else 0, pl))
                elif name == NEW[purpose] and not p_new.is_file() and \
                        _open_new(G, new, key, purpose, p.read_bytes()) is not None:
                    _write(p_new, p.read_bytes())
        have = _open_new(G, new, key, purpose, p_new.read_bytes()) if p_new.is_file() else None
        if have is not None:
            cands.append((at_of(have), 2, have))
        if not cands:
            continue
        best = max(cands, key=lambda c: (c[0], c[1]))
        best = best[2] if best[1] == 2 else with_item(best[2])
        if best is not None and have != best:
            env_ = sealed.seal_link(purpose, new, G.key_bytes(key), best)
            if sealed.open_link(purpose, new, G.key_bytes(key), env_) != best:
                raise RuntimeError("a carried-over envelope did not open to its payload")
            _write(p_new, env_)
    kept_picks = G.KEPT_ROOT / new / NEW["picks"]
    ph = sha(kept_picks.read_bytes()) if kept_picks.is_file() else ""
    stmts = [("UPDATE sessions SET gallery_id=? WHERE id=? AND gallery_id=?", (new, sid, old)),
             ("""UPDATE galleries SET link_was=?, was_open=?, was_fp=?, sealed_gen=1,
                 picks_hash=CASE WHEN ? != '' THEN ? ELSE picks_hash END WHERE session_id=?""",
              (old, 1 if was_open else 0, was_fp, ph, ph, sid)),
             ("UPDATE OR IGNORE sealed_seen SET link=? WHERE link=?", (new, old))]
    for t in ("print_galleries", "print_orders", "print_order_oids"):
        stmts.append((f"UPDATE {t} SET gid=? WHERE gid=?", (new, old)))
    _commit(env.ctx, stmts)
    env.st.setdefault("links", {})[str(sid)] = new
    env.save()
    settle_local(G, old, new)
    if audit:
        env.ctx.audit("The gallery moved to its new address. The link the client has keeps working: it leads to the new "
                      "one.", sid=sid, who="Lumen Aureum")
    return new

def settle_local(G, old, new):

    if not (G.GID_OK.match(old or "") and is_link(G, new)) or old == new:
        return
    src, dst = G.OUT_ROOT / old, G.OUT_ROOT / new
    if src.is_dir() and not dst.exists():
        src.rename(dst)
    if dst.is_dir() and (dst / "index.html").is_file():
        text = (dst / "index.html").read_text(errors="replace")
        want = refit.page_for(G, new, text, cws=G.page_cws(text) or old)
        if (dst / "index.html").read_bytes() != want:
            _write(dst / "index.html", want)
        for name in G.HOST_SCRIPTS:
            b = refit.script_bytes(G, name)
            p = dst / name
            if not p.is_file() or p.read_bytes() != b:
                _write(p, b)
    ko, kn = G.KEPT_ROOT / old, G.KEPT_ROOT / new
    if ko.is_dir():
        kn.mkdir(parents=True, exist_ok=True)
        for p in sorted(ko.iterdir()):
            t = kn / p.name
            if p.name in (NEW["picks"], NEW["draft"]):

                t = kn / (p.name + ".before-link")
            if t.exists():
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink()
                continue
            p.rename(t)
        try:
            ko.rmdir()
        except OSError:
            pass

def _copy(G, host, src, dst, name, local=None):

    ct = G.MIME.get(os.path.splitext(name)[1] or name)
    if local is not None and local.is_file():
        with open(local, "rb") as f:
            host.put_stream(f"{dst}/{name}", f, content_type=ct, cache_control=G.cache_for(name))
        return
    with tempfile.TemporaryFile() as tmp:
        host.get_stream(f"{src}/{name}", tmp)
        tmp.seek(0)
        host.put_stream(f"{dst}/{name}", tmp, content_type=ct, cache_control=G.cache_for(name))

PHOTO = re.compile(r"[0-9a-f]{24}\.bin")

def _foreign(env, r, host, to):

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    G, sid = env.G, r["sid"]
    taken = env.q("SELECT 1 FROM sessions WHERE gallery_id=? AND id!=?", (to, sid), one=True) or \
        env.q("SELECT 1 FROM galleries WHERE link_was=? AND session_id!=?", (to, sid), one=True) or \
        any(v == to and k != str(sid) for k, v in (env.st.get("links") or {}).items())
    if taken:
        return "its old address leads to another gallery's address; check that gallery's folders on the host"
    if hasattr(G, "know_key"):
        G.know_key(to, r["ekey"] or r["key"])
    blob = G._read(host, f"{to}/manifest.bin")
    try:
        AESGCM(G.key_bytes(r["ekey"] or r["key"])).decrypt(blob[:12], blob[12:], b"lumen-aureum-gallery:manifest.bin")
    except Exception:
        return "its old address leads to a folder that isn't this gallery; check that folder on the host"
    return None

def build_new(env, run, r, host, hfp, base, force=False):

    G = env.G
    old, sid = r["gallery_id"], r["sid"]
    ms = env.st.setdefault("moves", {}).setdefault(str(sid), {})
    page_now = G._read(host, f"{old}/index.html")
    text = (page_now or b"").decode("utf-8", "replace")
    to = re.search(r'data-to="(g-[A-Za-z0-9_-]{23})"', text[:300])
    if to and "gate" not in ms:

        why = _foreign(env, r, host, to.group(1))
        if why:
            raise refit.Wait("forward", why)
        there = G._read(host, f"{to.group(1)}/index.html")
        if there is None:
            raise refit.Wait("missing", "its page is not on the host")
        env.st.setdefault("links", {})[str(sid)] = to.group(1)
        t2 = there.decode("utf-8", "replace")
        ms.update(gate='data-gate="1"' in t2[:400], credit='id="la"' in t2, cws=G.page_cws(t2) or old)
        env.save()
    elif page_now is not None and not to:
        ms.update(gate='data-gate="1"' in text[:400], credit='id="la"' in text, cws=G.page_cws(text) or old)
        env.save()
    elif "gate" not in ms:
        raise refit.Wait("missing", "its page is not on the host")
    new = link_for(env, sid)
    if hasattr(G, "know_key"):
        G.know_key(new, r["ekey"] or r["key"])
    p = run.probe(hfp, host, base)
    if p.kind == "unreachable":
        raise refit.Wait(hostcheck.why(p), p.get("detail", ""))
    if p.kind == "old":
        raise refit.Wait("old-php", p.get("detail", ""))
    if not force and refit.busy(run, r, host):
        since = run.state.get("since") or run.now
        if run.now - since < refit.QUIET_MAX_DAYS * 86400:
            run.gal_state(old)["quiet"] = True
            raise refit.Wait("quiet", "Waiting to update this gallery while the client is picking")
    run.gal_state(old).pop("quiet", None)
    php = p.kind == "ok"
    want = G.viewer_page(new, gate=ms["gate"], credit=ms["credit"], cws=ms.get("cws") or old).encode()
    if php:
        for name in sorted(G.HOST_SCRIPTS, key=G.upload_order):
            if G._read(host, f"{new}/{name}") != refit.script_bytes(G, name):
                refit._put(G, host, new, name, refit.script_bytes(G, name))
    local = G.OUT_ROOT / old
    local_ok = local.is_dir() and G.online_is_current(old)
    names = {n.split("/", 1)[1] for n in host.list(f"{old}/") if n.startswith(f"{old}/")}
    for name in sorted(names, key=lambda n: (n != "limits.txt", n == "manifest.bin", n)):
        if name in NOT_COPIED or name in G.HOST_SCRIPTS or "/" in name:
            continue
        size = host.stat(f"{old}/{name}")
        if size is None:
            continue
        if name == "limits.txt":
            lim = G._read(host, f"{old}/limits.txt") or b""
            lim = b"".join(ln for ln in lim.splitlines(keepends=True) if not ln.startswith((b"moved=", b"updating=")))
            if php and b"proof=1" not in lim:
                lim = lim.rstrip(b"\n") + b"\nproof=1\n"
            if hasattr(G, "limits_flags"):
                flags = G.limits_flags(old).encode().splitlines(keepends=True)
                keys = {ln.split(b"=", 1)[0] for ln in flags}
                lim = b"".join(ln for ln in lim.splitlines(keepends=True) if ln.split(b"=", 1)[0].strip() not in keys)
                lim = lim.rstrip(b"\n") + b"\n" + b"".join(flags) if flags else lim
            if G._read(host, f"{new}/limits.txt") != lim:
                host.put_stream(f"{new}/limits.txt", io.BytesIO(lim), content_type="text/plain")
            continue
        if host.stat(f"{new}/{name}") == size and PHOTO.fullmatch(name):
            continue
        from_build = PHOTO.fullmatch(name) or (local_ok and name == "manifest.bin")
        _copy(G, host, old, new, name, local / name if from_build else None)
        if host.stat(f"{new}/{name}") != size:
            raise refit.Wait("readback", f"{name} did not copy whole")
    if G._read(host, f"{new}/index.html") != want:
        refit._put(G, host, new, "index.html", want)
    if G._read(host, f"{new}/index.html") != want:
        raise refit.Wait("readback", "the page read back is not the one just put there")
    refit._https_page(run, new, base, want, run.gal_state(new))
    rr = dict(r)
    rr["gallery_id"] = new
    if php and not refit.gate_closed(G, host, new, run.now):
        why = refit.canary(run, rr, host, base, want)
        if why:
            raise refit.Wait("canary", why)
    return new

def door(env, host, base, folder, check, scripts=True, mark=b"moved=1"):

    G = env.G
    lim = G._read(host, f"{folder}/limits.txt")
    for name in sorted(G.HOST_SCRIPTS, key=G.upload_order) if scripts else ():
        if name == ".htaccess" or (name == "orders.php" and host.stat(f"{folder}/orders.php") is None):
            continue
        if G._read(host, f"{folder}/{name}") != refit.script_bytes(G, name):
            refit._put(G, host, folder, name, refit.script_bytes(G, name))
    if not scripts:
        mark = b"moved=1"
    if lim is not None and not re.search(rb"(?m)^(moved|updating)=", lim):
        add = b"\n" + mark + b"\n"
        if b"contact=" not in lim and G.contact_email():
            add += f'contact="{G.contact_email()}"\n'.encode()
        host.put_stream(f"{folder}/limits.txt", io.BytesIO(lim.rstrip(b"\n") + add), content_type="text/plain")
    if not check:
        return True
    return hostcheck.door_closed(base, folder, G.HOST_SCRIPTS_SET, wait=2)

def empty_old(env, r, host, folder, keep_page, read, deletes=True):

    G = env.G
    left = unread = False
    for name in SENT:
        for _ in range(3):
            blob = G._read(host, f"{folder}/{name}")
            if blob is None:
                break
            if read.get(name + "?") == sha(blob):
                unread = True
                break
            if read.get(name) == sha(blob):
                if deletes:
                    host.delete(f"{folder}/{name}")
                else:
                    unread = True
                break
            read.update(take_in(env, r, host, folder))
        else:
            left = True
    if keep_page:
        for n in host.list(f"{folder}/"):
            rest = n.split("/", 1)[1] if "/" in n else n
            if rest == "index.html" or rest in SENT or rest.startswith("orders/"):
                continue
            host.delete(n)
        if host.stat(f"{folder}/.htaccess") is not None and not unread:
            host.delete(f"{folder}/.htaccess")
        if not [n for n in host.list(f"{folder}/orders/")]:
            host.remove_dir(f"{folder}/orders/")
    return not left

def put_redirect(env, host, old, gid):

    G = env.G
    page = G.redirect_page(gid)
    if G._read(host, f"{old}/index.html") != page:
        host.put_stream(f"{old}/index.html", io.BytesIO(page), content_type=G.MIME[".html"], cache_control=G.CACHE_FRESH)
    if G._read(host, f"{old}/index.html") != page:
        raise refit.Wait("readback", "the forwarding page read back differently")

def merge_once(env, host, old, gid):

    G = env.G
    theirs = G._read(host, f"{old}/once.txt")
    if not theirs or len(theirs) > 65536:
        return
    got = [n for n in theirs.decode("ascii", "replace").split("\n") if PHOTO.fullmatch(n)]
    for _ in range(3):
        here = G._read(host, f"{gid}/once.txt")
        have = [n for n in (here or b"").decode("ascii", "replace").split("\n") if n]
        add = [n for n in got if n not in have]
        if not add:
            return
        want = ("\n".join(have + add) + "\n").encode()
        if G._read(host, f"{gid}/once.txt") != here:
            continue
        host.put_stream(f"{gid}/once.txt", io.BytesIO(want), content_type="text/plain")
        now = G._read(host, f"{gid}/once.txt") or b""
        if all(n.encode() in now.split(b"\n") for n in add):
            return
    raise refit.Wait("readback", "the one-time download's list read back differently")

def old_phase(env, only_sid=None):

    G, q, x = env.G, env.q, env.x
    rows = q(ROW + " WHERE g.was_open=1 AND g.link_was IS NOT NULL")
    waits = env.st.setdefault("waits", {})
    for r in rows:
        if only_sid and r["sid"] != only_sid:
            continue
        old, gid, hfp = r["link_was"], r["gallery_id"], r["was_fp"] or ""
        cfg = G.host_cfg_for(hfp) if hfp else None
        if cfg is None:
            x("UPDATE galleries SET was_open=0 WHERE session_id=?", (r["sid"],))
            waits.pop(old, None)
            continue
        try:
            host = G.open_host(cfg)
            with host.session():
                if not host.list(f"{old}/") and host.stat(f"{old}/index.html") is None:
                    x("UPDATE galleries SET was_open=0 WHERE session_id=?", (r["sid"],))
                    waits.pop(old, None)
                    continue
                base = env.base_for(r, hfp)
                beside = host.stat(f"{gid}/index.html") is not None
                p = hostcheck.probe(host, base) if base else None
                if p is not None and p.kind == "unreachable":
                    raise refit.Wait(hostcheck.why(p), p.get("detail", ""))
                kind = p.kind if p is not None else ""
                check = kind == "ok" and beside
                proved = kind == "none"
                if kind in ("ok", "old") or (not kind and host.stat(f"{old}/limits.txt") is not None):

                    if not door(env, host, base, old, check=check, scripts=kind != "old",
                                mark=b"updating=1" if beside else b"moved=1"):
                        if beside:
                            put_redirect(env, host, old, gid)
                        raise refit.Wait("door", "the old folder's scripts are not running yet (the host may keep the "
                                                 "old ones a few minutes)")
                    proved = proved or check
                if not beside:
                    moved_copy_page(env, host, old, gid)
                try:

                    G.final_collect(old, host)
                    read = take_in(env, r, host, old, put_host=host if beside else None)
                    for name in ("seen.txt", "done.txt"):
                        theirs = G._read(host, f"{old}/{name}")
                        if beside and theirs and host.stat(f"{gid}/{name}") is None:
                            host.put_stream(f"{gid}/{name}", io.BytesIO(theirs), content_type="text/plain")
                    if beside:
                        put_redirect(env, host, old, gid)
                        if proved and host.stat(f"{old}/gate.php") is not None:
                            host.delete(f"{old}/gate.php")
                        merge_once(env, host, old, gid)
                except refit.Wait:
                    if beside:
                        put_redirect(env, host, old, gid)
                    raise
                if not empty_old(env, r, host, old, keep_page=beside, read=read, deletes=proved):
                    raise refit.Wait("left", "a file a client sent to its old folder isn't read yet")
                if not proved and any(n in read for n in SENT):
                    _kept_there(env, r, hfp)
            x("UPDATE galleries SET was_open=0 WHERE session_id=?", (r["sid"],))
            waits.pop(old, None)
            env.draft_status(r["sid"])
        except refit.Wait as w:
            waits[old] = dict(host=hfp, why=w.why, words=w.words)
        except Exception as e:
            env.ctx.app.logger.exception("upgrade 1.4: the old folder of %s", gid)
            waits[old] = dict(host=hfp, why="unreachable", words=errtext.words(e, 120))
        env.save()

def _kept_there(env, r, hfp):

    done = env.st.setdefault("kept_there", [])
    tag = f"{r['sid']}:{hfp}"
    if tag in done:
        return
    done.append(tag)
    env.ctx.audit(f"What the client sent to the copy on {_label(env.G, hfp)} was read and carried over; those files stay "
                  "there until that copy comes down, since that host couldn't show its old scripts had stopped.",
                  sid=r["sid"], who="Lumen Aureum")

def local_phase(env):

    G, q, x = env.G, env.q, env.x
    for r in q(ROW + " WHERE g.link_was IS NOT NULL"):
        settle_local(G, r["link_was"], r["gallery_id"])
    online = {r["sid"] for r in online_rows(q)}
    for r in q(ROW):
        gid = r["gallery_id"] or ""
        if is_link(G, gid) or r["sid"] in online:
            continue
        if not G.GID_OK.match(gid):
            x("UPDATE sessions SET gallery_id=? WHERE id=?", (link_for(env, r["sid"]), r["sid"]))
            continue
        partial = r["upload_state"] == "error" and not r["uploaded_at"] and r["host_fp"]
        relink(env, r, audit=False)
        if partial:
            x("INSERT OR IGNORE INTO gallery_leftovers (gallery_id, host_fp, added) VALUES (?,?,?)",
              (gid, r["host_fp"], date.today().isoformat()))
    for r in q("SELECT s.id, s.gallery_id FROM sessions s WHERE NOT EXISTS (SELECT 1 FROM galleries g WHERE g.session_id=s.id)"):
        if not is_link(G, r["gallery_id"] or ""):
            x("UPDATE sessions SET gallery_id=? WHERE id=?", (link_for(env, r["id"]), r["id"]))
    x("""UPDATE galleries SET sealed_gen=1 WHERE sealed_gen=0 AND NOT (removed_at IS NULL AND
         (upload_state='up' OR (upload_state='error' AND uploaded_at IS NOT NULL)))""")
    for r in q(ROW + " WHERE g.sealed_gen=0"):
        if is_link(G, r["gallery_id"] or ""):
            x("UPDATE galleries SET sealed_gen=1 WHERE session_id=?", (r["sid"],))
    env.st["local"] = "done"
    env.save()

def _label(G, hfp):
    cfg = G.host_cfg_for(hfp)
    return G.host_label(cfg) if cfg else "an earlier web host"

def move_one(env, run, r, host, hfp, base, force=False):

    take_in(env, r, host, r["gallery_id"])
    build_new(env, run, r, host, hfp, base, force=force)
    run.x("UPDATE galleries SET page_set=? WHERE session_id=?", (run.fp, r["sid"]))
    new = relink(env, r, was_fp=hfp, was_open=True)
    old_phase(env, only_sid=r["sid"])
    env.draft_status(r["sid"])
    return new

def gallery_phase(env, only_sid=None, force=False):

    G, q, x = env.G, env.q, env.x
    rows = [r for r in online_rows(q) if not is_link(G, r["gallery_id"] or "") and G.GID_OK.match(r["gallery_id"] or "")
            and (not only_sid or r["sid"] == only_sid)]
    cur = G.host_fingerprint() if G.has_host() else ""
    by = {}
    for r in rows:
        by.setdefault(r["host_fp"] or cur, []).append(r)
    waits = env.st.setdefault("waits", {})
    done = 0
    rstate = refit.load_state(q)
    run = refit.Run(G, q, x, env.ctx.audit, "", rstate)
    rstate.setdefault("since", run.now)
    rstate["fp"] = run.fp
    for hfp, rs in by.items():
        cfg = G.host_cfg_for(hfp) if hfp else None
        if cfg is None:
            for r in rs:
                if hfp:
                    relink(env, r, audit=False)
                    env.ctx.audit("Updated to 1.4 without its host: that web host was forgotten as gone for good, so "
                                  "nothing could be read from it", sid=r["sid"], who="Lumen Aureum")
                    waits.pop(r["gallery_id"], None)
            continue
        try:
            host = G.open_host(cfg)
        except Exception as e:
            for r in rs:
                waits[r["gallery_id"]] = dict(host=hfp, why="unreachable", words=errtext.words(e, 120))
            continue
        try:
            with host.session():
                for r in rs:
                    base = env.base_for(r, hfp)
                    with LOCK:
                        try:
                            move_one(env, run, r, host, hfp, base, force=force or
                                     r["gallery_id"] in (rstate.get("now") or []))
                            waits.pop(r["gallery_id"], None)
                            done += 1
                        except refit.Wait as w:
                            waits[r["gallery_id"]] = dict(host=hfp, why=w.why, words=w.words)
                            if w.why in ("unreachable", "old-php", "canary") + hostcheck.CERT_WAITS:
                                for r2 in rs:
                                    waits.setdefault(r2["gallery_id"], dict(host=hfp, why=w.why, words=w.words))
                                break
                    env.save()
                    refit.save_state(x, rstate)
        except Exception as e:
            env.ctx.app.logger.exception("upgrade 1.4: web host %s", hfp)
            for r in rs:
                waits.setdefault(r["gallery_id"], dict(host=hfp, why="unreachable", words=errtext.words(e, 120)))
        if all(waits.get(r["gallery_id"]) is None for r in rs):
            env.ctx.hooks.fire("host_refit", hfp)
    env.save()
    refit.save_state(x, rstate)
    return done

def relink_for_move(ctx, G, sid):

    with LOCK:
        env = Env(ctx, G, ctx.app.config.get("studio_page_upgrade"))
        r = _row_sid(env.q, sid)
        if r is None or is_link(G, r["gallery_id"] or ""):
            return r["gallery_id"] if r else ""
        return relink(env, r, was_fp=r["host_fp"], was_open=bool(r["host_fp"]))

def moved_copy_page(env, host, folder, gid):

    G = env.G
    page = G._read(host, f"{folder}/index.html")
    text = (page or b"").decode("utf-8", "replace")
    if not page or 'data-gate="1"' not in text[:400] or 'data-to="' in text[:300]:
        return False
    want = G.viewer_page(gid, gate=True, credit='id="la"' in text, cws=G.page_cws(text) or folder).encode()
    if page != want:
        host.put_stream(f"{folder}/index.html", io.BytesIO(want), content_type=G.MIME[".html"], cache_control=G.CACHE_FRESH)
    return True

def hand_over_old(ctx, G, sid, old, new):

    with LOCK:
        env = Env(ctx, G, ctx.app.config.get("studio_page_upgrade"))
        r = _row_sid(env.q, sid)
        if r is None or not r["link_was"] or not is_link(G, r["gallery_id"] or ""):
            return False
        folder = r["link_was"]
        with old.session():
            if old.stat(f"{folder}/limits.txt") is None and not old.list(f"{folder}/"):
                env.x("UPDATE galleries SET was_open=0 WHERE session_id=?", (sid,))
                return True
            door(env, old, "", folder, check=False)
            moved_copy_page(env, old, folder, r["gallery_id"])
            G.final_collect(folder, old)
            read = take_in(env, r, old, folder, put_host=new)

            if not empty_old(env, r, old, folder, keep_page=False, read=read, deletes=False):
                return False
            if any(n in read for n in SENT):
                _kept_there(env, r, r["was_fp"] or r["moved_from"] or "")
                env.save()
        env.x("UPDATE galleries SET was_open=0 WHERE session_id=?", (sid,))
        return True

def _earlier_done(st, hfp, gid):

    v = st.get(hfp)
    return isinstance(v, dict) and v.get(gid) == "done"

def _mark_earlier(st, hfp, gid, what):
    v = st.get(hfp)
    if not isinstance(v, dict):
        v = st[hfp] = {}
    v[gid] = what

def earlier_phase(env):

    G, q = env.G, env.q
    st = env.st.setdefault("earlier", {})
    cur = G.host_fingerprint() if G.has_host() else ""
    rows = q(ROW + " WHERE g.moved_from IS NOT NULL AND g.removed_at IS NULL AND g.upload_state='up'")
    by = {}
    for r in rows:
        if r["moved_from"] != cur and not _earlier_done(st, r["moved_from"], r["gallery_id"]) and \
                is_link(G, r["gallery_id"] or "") and not r["was_open"]:
            by.setdefault(r["moved_from"], []).append(r)
    for hfp, rs in by.items():
        cfg = G.host_cfg_for(hfp)
        if cfg is None:
            for r in rs:
                _mark_earlier(st, hfp, r["gallery_id"], "done")
            continue
        try:
            old = G.open_host(cfg)
            here = G.gallery_host() if G.has_host() else None
            with old.session():
                for r in rs:
                    for folder in dict.fromkeys(f for f in (r["link_was"], r["gallery_id"]) if f):
                        if G._read(old, f"{folder}/limits.txt") is None:
                            continue
                        door(env, old, "", folder, check=False)
                        if folder != r["gallery_id"]:
                            moved_copy_page(env, old, folder, r["gallery_id"])
                        read = take_in(env, r, old, folder, put_host=here)
                        if not empty_old(env, r, old, folder, keep_page=False, read=read, deletes=False):
                            raise RuntimeError("a file a client sent there isn't read yet")
                        if any(n in read for n in SENT):
                            _kept_there(env, r, hfp)
                    _mark_earlier(st, hfp, r["gallery_id"], "done")
                    env.save()
        except Exception:
            env.ctx.app.logger.exception("upgrade 1.4: earlier host %s", hfp)
            for r in rs:
                if not _earlier_done(st, hfp, r["gallery_id"]):
                    _mark_earlier(st, hfp, r["gallery_id"], "waiting")
        env.save()

def site_phase(env):

    if env.st.get("site") == "done":
        return True
    s = env.studio
    if s is None or not s["form_online"]():
        env.st["site"] = "done"
        env.save()
        return True
    try:
        host, ih, base, kind = s["hosts"]()
    except Exception as e:
        env.st["site_wait"] = dict(why="unreachable", words=errtext.words(e, 120))
        env.save()
        return False
    if kind != "php":
        env.st["site"] = "done"
        env.save()
        return True
    p = hostcheck.probe(host, base)
    if p.kind != "ok":
        env.st["site_wait"] = dict(why={"none": "no-php", "unreachable": hostcheck.why(p)}.get(p.kind, p.kind),
                                   words=p.get("detail", ""))
        env.save()
        return False
    try:
        s["put_scripts"](host)
        if not _wait_script(hostcheck.join(base, "inquiry.php"), hostcheck.SCRIPT_SET, p.get("wait", 2)):
            env.st["site_wait"] = dict(why="door", words="the new inquiry script is not running yet")
            env.save()
            return False
        res = s["sweep"](ih, legacy_130.open_inquiry, env.st.setdefault("site_shas", {}))
        s["put_form"](host)
    except Exception as e:
        env.ctx.app.logger.exception("upgrade 1.4: studio page")
        env.st["site_wait"] = dict(why="unreachable", words=errtext.words(e, 120))
        env.save()
        return False
    env.st.pop("site_wait", None)
    env.st["site"] = "done"
    env.st["site_left"] = res.get("left", 0)
    env.save()
    return True

def _wait_script(url, want, wait):
    import time
    t0 = time.monotonic()
    while True:
        if hostcheck.script_answers(url, want):
            return True
        if time.monotonic() - t0 + wait > hostcheck.DOOR_LIMIT:
            return False
        hostcheck.SLEEP(max(1, wait))

def _left(env):

    G, q = env.G, env.q
    n = sum(1 for r in online_rows(q) if not is_link(G, r["gallery_id"] or ""))
    n += sum(1 for r in q("SELECT gallery_id FROM sessions") if not is_link(G, r["gallery_id"] or ""))
    n += q("SELECT COUNT(*) AS n FROM galleries WHERE was_open=1", one=True)["n"]
    return n

def finish(env):

    G, q = env.G, env.q
    if _left(env):
        return False
    cur = G.host_fingerprint() if G.has_host() else ""
    for r in q("""SELECT g.moved_from, s.gallery_id FROM galleries g JOIN sessions s ON s.id=g.session_id
                  WHERE g.moved_from IS NOT NULL AND g.removed_at IS NULL AND g.upload_state='up'"""):
        if r["moved_from"] != cur and not _earlier_done(env.st.get("earlier") or {}, r["moved_from"], r["gallery_id"]) \
                and G.host_cfg_for(r["moved_from"]):
            return False
    if env.st.get("site") != "done":
        return False
    kept_left = []
    keys = {r["gallery_id"]: (r["ekey"] or r["key"]) for r in q(
        "SELECT s.gallery_id, g.key, g.ekey FROM galleries g JOIN sessions s ON s.id=g.session_id WHERE s.gallery_id IS NOT NULL")}
    if G.KEPT_ROOT.is_dir():
        for d in sorted(G.KEPT_ROOT.iterdir()):
            if not d.is_dir():
                continue
            for purpose in ("picks", "draft"):
                leg, new = d / LEGACY[purpose], d / NEW[purpose]
                if not leg.is_file():
                    continue
                key = keys.get(d.name)
                ok = False
                if key and new.is_file():
                    try:
                        payload = legacy_130.open_picks_payload(leg.read_bytes(), key, LEGACY[purpose])
                        have = _open_new(G, d.name, key, purpose, new.read_bytes())
                        ok = have is not None and (json.loads(have) == json.loads(with_item(payload)) or
                                                   at_of(have) >= at_of(payload))
                    except ValueError:
                        ok = False
                if ok:
                    leg.unlink()
                else:
                    kept_left.append(f"{d.name}/{LEGACY[purpose]}")
    if env.studio is not None:
        try:
            env.studio["drop_unreadable"](env.st.get("site_shas") or {})
        except Exception:
            env.ctx.app.logger.exception("upgrade 1.4: leftover inquiry files")
            return False
    env.st["finished"] = date.today().isoformat()
    env.st.pop("waits", None)
    env.save()
    env.ctx.audit("Every gallery now has its link ID, and every gallery and the studio page use the 1.4 sealed format" + (
        f"; kept as they were, with nothing newer beside them: {', '.join(kept_left[:10])}" if kept_left else ""),
        who="Lumen Aureum")
    return True

def nothing_to_do(env):

    G, q = env.G, env.q
    if q("SELECT 1 FROM galleries WHERE uploaded_at IS NOT NULL OR upload_state != '' LIMIT 1", one=True):
        return False
    if G.KEPT_ROOT.is_dir() and any(G.KEPT_ROOT.iterdir()):
        return False
    if env.studio is not None and env.studio["form_online"]():
        return False
    return True

def quiet(ctx, G):

    if G is None:
        return False
    try:
        env = Env(ctx, G, ctx.app.config.get("studio_page_upgrade"))
        return not env.st and nothing_to_do(env)
    except Exception:
        return False

def run(ctx, G, job=None, only_sid=None, force=False):

    env = Env(ctx, G, ctx.app.config.get("studio_page_upgrade"))
    if env.st.get("finished"):
        return "Already done."
    if G is None:
        if site_phase(env):
            env.st.update(local="done", finished=date.today().isoformat())
            env.save()
            return "The inquiry form is updated."
        return "Waiting for the studio page's host."
    with LOCK:
        quiet = not env.st and nothing_to_do(env)
        local_phase(env)
        if quiet:
            env.st.update(local="done", site="done", finished=date.today().isoformat(), quiet=True)
            env.save()
            return "Nothing to update."
    if G.has_host() or any(not is_link(G, r["gallery_id"] or "") for r in online_rows(env.q)):
        gallery_phase(env, only_sid=only_sid, force=force)
    with LOCK:
        old_phase(env)
    earlier_phase(env)
    site_phase(env)
    if finish(env):
        return "Every gallery is updated."
    lines = status_line(env.q, G)
    return lines[0] if lines else "Waiting."

def ensure(ctx, G, sid):

    r = ctx.q("SELECT gallery_id FROM sessions WHERE id=?", (sid,), one=True)
    if not r or is_link(G, r["gallery_id"] or ""):
        return
    with LOCK:
        env = Env(ctx, G, ctx.app.config.get("studio_page_upgrade"))
        local_phase(env)
        r = _row_sid(env.q, sid)
        if r is None or is_link(G, r["gallery_id"] or ""):
            return
        gallery_phase(env, only_sid=sid, force=True)
        gid = (ctx.q("SELECT gallery_id FROM sessions WHERE id=?", (sid,), one=True) or {"gallery_id": ""})["gallery_id"]
        if not is_link(G, gid):
            why = (env.st.get("waits") or {}).get(gid) or {}
            if why.get("why") in hostcheck.CERT_WAITS:
                raise RuntimeError("the gallery online must first move to its new address (1.4), and that waits for "
                                   "the web host's certificate. " + webtls.words(why["why"][5:]))
            raise RuntimeError("the gallery online must first move to its new address (1.4), and that couldn't finish "
                               "now: " + (why.get("words") or why.get("why") or "its host can't be reached"))

def status_line(q, G):

    st = load(q)
    if not st or st.get("finished"):
        return []
    rows = online_rows(q)
    total = len(rows)
    left = [r for r in rows if not is_link(G, r["gallery_id"] or "") or r["was_open"]]
    out = []
    waits = st.get("waits") or {}

    def wait_of(r):
        return waits.get(r["gallery_id"]) or waits.get(r["link_was"] or "") or {}
    if total:
        by = {}
        for r in left:
            w = wait_of(r)
            by.setdefault((w.get("host") or r["host_fp"] or "", w.get("why") or ""), []).append(r)
        bits = []
        for (hfp, why), rs in by.items():
            lab = _label(G, hfp) if hfp else "its host"
            n = len(rs)
            wait = "waits" if n == 1 else "wait"
            w = wait_of(rs[0])
            if why in hostcheck.CERT_WAITS:
                bits.append(f"{n} {wait} for {lab}. " + webtls.words(why[5:]))
            elif why == "unreachable":
                bits.append(f"{n} {wait} for {lab}, which cannot be reached: check its settings. Saying it is gone for good "
                            "gives up any picks clients sent there that the app has not read.")
            elif why == "old-php":
                bits.append(f"{n} {wait} for {lab}: {w.get('words') or 'its PHP is older than 7.4'}.")
            elif why == "quiet":
                bits.append(f"{n} {wait} while the client is picking.")
            elif why:
                bits.append(f"{n} {wait} for {lab} ({w.get('words') or why}).")
            else:
                bits.append(f"{n} still to do.")
        out.append(f"Updating galleries: {total - len(left)} of {total} done." + (" " + " ".join(bits) if bits else ""))
    sw = st.get("site_wait")
    if sw and st.get("site") != "done":
        out.append("Updating the studio page's inquiry form: " + (
            "its host cannot be reached; inquiries sent meanwhile wait there." if sw.get("why") == "unreachable" else
            "it waits for its web host. " + webtls.words(sw["why"][5:]) if sw.get("why") in hostcheck.CERT_WAITS else
            (sw.get("words") or sw.get("why")) + "."))
    return out
