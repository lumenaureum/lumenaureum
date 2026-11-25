import hashlib
import io
import json
import time
from datetime import datetime

import hostcheck
import hostfiles

QUIET_HOURS = 6
CANARY_TRIES = 3
QUIET_MAX_DAYS = 7
CACHE_GIVE_UP_HOURS = 6
STATE_KEY = "gallery_refit"

def fingerprint(G):

    h = hashlib.sha256((G.ASSETS / "viewer.html").read_bytes())
    h.update(getattr(G, "CREDIT_HTML", "").encode())
    for name in sorted(G.HOST_SCRIPTS):
        h.update(name.encode() + b"\0" + (G.ASSETS / G.HOST_SCRIPTS[name]).read_bytes())
    return h.hexdigest()[:16]

def script_bytes(G, name):
    return (G.ASSETS / G.HOST_SCRIPTS[name]).read_bytes()

def flags(text):

    return 'data-gate="1"' in text[:400], 'id="la"' in text

def page_for(G, gid, text, cws=None):

    gate, credit = flags(text)
    return G.viewer_page(gid, gate=gate, credit=credit, cws=G.page_cws(text) if cws is None else cws).encode()

class Steps:

    def after_door(self, run, r, host):
        pass

    def before_page(self, run, r, host):
        pass

    def record(self, run, r, host):
        pass

    def after(self, run, r, host):
        pass

    def static(self, run, r, host):
        pass

class Wait(Exception):

    def __init__(self, why, words=""):
        super().__init__(why)
        self.why, self.words = why, words

class Run:

    def __init__(self, G, q, x, audit, base_url, state, now=None):
        self.G, self.q, self.x, self.audit, self.base_url = G, q, x, audit, base_url
        self.state = state
        self.now = now or time.time()
        self.probes = {}
        self.fp = fingerprint(G)

    def host_state(self, hfp):
        return self.state.setdefault("hosts", {}).setdefault(hfp, {})

    def gal_state(self, gid):
        return self.state.setdefault("galleries", {}).setdefault(gid, {})

    def probe(self, hfp, host, base):
        if hfp not in self.probes:
            self.probes[hfp] = hostcheck.probe(host, base)
            self.host_state(hfp).update(php=self.probes[hfp].kind, version=self.probes[hfp].get("version", ""),
                                        detail=self.probes[hfp].get("detail", ""))
        return self.probes[hfp]

def load_state(q, key=STATE_KEY):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    try:
        st = json.loads(r["value"]) if r and r["value"] else {}
    except ValueError:
        st = {}
    return st if isinstance(st, dict) else {}

def save_state(x, st, key=STATE_KEY):
    x("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(st, sort_keys=True)))

def _put(G, host, gid, name, data):
    host.put_stream(f"{gid}/{name}", io.BytesIO(data), content_type=G.MIME.get("." + name.rsplit(".", 1)[-1], G.MIME.get(name)),
                    cache_control=G.cache_for(name))

def busy(run, r, host):

    gid = r["gallery_id"]

    if ("stage" in r.keys() and r["stage"] in ("closed", "lost")) or run.G.online_download(gid):
        return False
    cut = run.now - QUIET_HOURS * 3600
    at = r["draft_at"] if "draft_at" in r.keys() else None
    if at:
        try:
            if datetime.fromisoformat(at).timestamp() > cut:
                return True
        except ValueError:
            pass
    if not (r["retouch_picks"] or r["favorites"] or r["veto"]) if "retouch_picks" in r.keys() else False:
        seen = run.G._read(host, f"{gid}/seen.txt")
        if seen:
            try:
                t = datetime.fromisoformat(seen.decode().strip().replace("Z", "+00:00")).timestamp()
                if t > cut:
                    return True
            except ValueError:
                pass
    return False

def keep_prev(run, gid, host, page, scripts, limits):

    d = run.G.KEPT_ROOT / gid / "prev"
    try:
        if (d / ".for").read_text().strip() == run.fp:
            return
    except OSError:
        pass
    import shutil
    tmp = run.G.KEPT_ROOT / gid / ".prev.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    if page is not None:
        (tmp / "index.html").write_bytes(page)
    for n, b in scripts.items():
        if b is not None:
            (tmp / n).write_bytes(b)
    if limits is not None:
        (tmp / "limits.txt").write_bytes(limits)
    (tmp / ".for").write_text(run.fp + "\n")
    shutil.rmtree(d, ignore_errors=True)
    tmp.rename(d)

def roll_back(run, gid, host):

    G = run.G
    d = G.KEPT_ROOT / gid / "prev"
    if not d.is_dir():
        return False
    hostfiles.unlayout(host, gid)
    raw = hostfiles.unwrap(host)
    names = sorted((p.name for p in d.iterdir() if p.is_file() and p.name not in (".for", "limits.txt")),
                   key=G.upload_order)
    for n in names:
        _put(G, host, gid, n, (d / n).read_bytes())
    prev_lim = d / "limits.txt"
    if prev_lim.is_file() and b"proof=1" not in prev_lim.read_bytes():
        cur = G._read(raw, f"{gid}/limits.txt")
        if cur is not None and b"proof=1" in cur:
            _put(G, raw, gid, "limits.txt",
                 b"".join(ln for ln in cur.splitlines(keepends=True) if ln.strip() != b"proof=1"))
    return True

def _ini(raw):

    out = {}
    for ln in raw.decode("utf-8", "replace").splitlines():
        ln = ln.strip()
        if not ln or ln[0] in ";#[" or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] == '"':
            v = v[1:-1]
        out[k.strip()] = v
    return out

def gate_closed(G, host, gid, now=None):

    from datetime import timezone
    now = now or time.time()
    raw = G._read(host, f"{gid}/limits.txt")
    if raw is None:
        return True
    lim = _ini(raw)
    if "days" not in lim or "unopened_until" not in lim:
        return True

    def day_end(ts, day):
        if lim.get(ts, "").isdigit():
            return int(lim[ts])
        if day in lim:
            try:
                return datetime.fromisoformat(lim[day] + "T23:59:59+00:00").timestamp()
            except ValueError:
                return None
        return None
    try:
        days = int(lim["days"])
    except ValueError:
        days = 0
    seen = G._read(host, f"{gid}/seen.txt")
    end = None
    if seen and seen.strip():
        try:
            d = datetime.fromisoformat(seen.decode().strip().replace("Z", "+00:00"))
            end = (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() + 86400 * days
        except ValueError:
            end = None
    else:
        end = day_end("unopened_ts", "unopened_until")
    if "until" in lim or "until_ts" in lim:
        end = day_end("until_ts", "until")
    done = G._read(host, f"{gid}/done.txt")
    if done is not None and done.strip().isdigit() and end is not None and int(done.strip()) < end:
        end = int(done.strip())
    return end is None or now > end

def flags_up(G, host, gid):

    lim = G._read(host, f"{gid}/limits.txt")
    if lim is None or not hasattr(G, "limits_flags"):
        return
    want = G.limits_flags(gid).encode().splitlines(keepends=True)
    keys = {ln.split(b"=", 1)[0] for ln in want}
    out = b"".join(ln for ln in lim.splitlines(keepends=True) if ln.split(b"=", 1)[0].strip() not in keys)
    out = out.rstrip(b"\n") + b"\n" + b"".join(want)
    if out != lim:
        _put(G, host, gid, "limits.txt", out)

def gate_seen(run, ok):

    st = load_state(run.q, "gallery_host_caps")
    now = st.get("now")
    if not isinstance(now, dict) or now.get("gate") is ok:
        return
    now["gate"] = ok
    import hostcheck
    if ok is False:
        now["features"] = [[k, lb, False, hostcheck.GATE_NOTE] if k in hostcheck.GATED and w else [k, lb, w, n]
                           for k, lb, w, n in (now.get("features") or [])]
    save_state(run.x, st, "gallery_host_caps")

def canary(run, r, host, base, page):

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    G, gid = run.G, r["gallery_id"]
    key = G.key_bytes(r["ekey"] or r["key"])
    st, body = hostcheck.fetch(hostcheck.join(base, f"{gid}/index.html"))
    if st != 200 or body != page:
        return "the page read over https is not the new one"
    gate, _ = flags(page.decode("utf-8", "replace"))
    proof = hashlib.sha256(key).hexdigest()[:32]

    def get(name):
        u = hostcheck.join(base, f"{gid}/gate.php?f={name}" if gate else
                           f"{gid}/{hostfiles.name_for(proof, name) if name in hostfiles.GATED else name}")
        st_, b_ = hostcheck.fetch(u, headers={"X-Proof": proof} if gate else None)
        if st_ != 200:
            raise ValueError(f"{name} answered {st_}")
        return AESGCM(key).decrypt(b_[:12], b_[12:], ("lumen-aureum-gallery:" + name).encode())
    try:
        m = json.loads(get("manifest.bin"))
        items = m.get("items") or []
        if items:
            get(items[0]["t"])
    except Exception as e:
        return f"the photos did not open through the new gate ({str(e)[:60] or type(e).__name__})"
    if gate:

        st, b_ = hostcheck.fetch(hostcheck.join(base, f"{gid}/{hostfiles.name_for(proof, 'manifest.bin')}"))
        if st in (200, 401, 403):
            gate_seen(run, not (st == 200 and b_))
        try:
            st, h, _b = hostcheck.HTTP("POST", hostcheck.join(base, f"{gid}/picks.php"), data=b"d=AAAA",
                                       headers={"Content-Type": "application/x-www-form-urlencoded", "X-Proof": proof})
        except Exception:
            st, h = 0, {}
        if st != 400 or h.get("x-host-scripts", "") != G.HOST_SCRIPTS_SET:
            return f"picks.php did not refuse a malformed body as it should ({st})"
        try:
            st, h, _b = hostcheck.HTTP("POST", hostcheck.join(base, f"{gid}/orders.php"), data=b"d=AAAA&w=0000000000000000",
                                       headers={"Content-Type": "application/x-www-form-urlencoded", "X-Proof": proof})
        except Exception:
            st, h = 0, {}
        if st != 400 or h.get("x-host-scripts", "") != G.HOST_SCRIPTS_SET:
            return f"orders.php did not refuse a malformed body as it should ({st})"
    return ""

def refit_gallery(run, r, host, hfp, base, steps=None, force=False, need_page=True):

    G, steps = run.G, steps or Steps()
    gid = r["gallery_id"]
    hs, gs = run.host_state(hfp), run.gal_state(gid)
    if hs.get("failed_fp") == run.fp:
        raise Wait("canary", hs.get("failed_words", ""))
    page_now = G._read(host, f"{gid}/index.html")
    if page_now is None and need_page:
        raise Wait("missing", "its page is not on the host")
    want = page_for(G, gid, (page_now or b"").decode("utf-8", "replace") if page_now else
                    (G.OUT_ROOT / gid / "index.html").read_text())
    p = run.probe(hfp, host, base)
    if p.kind == "unreachable":
        raise Wait(hostcheck.why(p), p.get("detail", ""))
    if p.kind == "old":
        raise Wait("old-php", p.get("detail", ""))
    if p.kind == "none":
        steps.static(run, r, host)
        hostfiles.relayout(host, gid, remove=False)
        if page_now != want:
            _put(G, host, gid, "index.html", want)
        if G._read(host, f"{gid}/index.html") != want:
            raise Wait("readback", "the page read back is not the one just put there")
        hostfiles.relayout(host, gid)
        _https_page(run, gid, base, want, gs)
        run.x("UPDATE galleries SET page_set=? WHERE session_id=?", (run.fp, r["sid"]))
        steps.record(run, r, host)
        steps.after(run, r, host)
        return "done"
    scripts_now = {n: G._read(host, f"{gid}/{n}") for n in G.HOST_SCRIPTS}
    current = page_now == want and all(scripts_now[n] == script_bytes(G, n) for n in G.HOST_SCRIPTS)
    if not current and not force and gid not in (run.state.get("now") or []) and busy(run, r, host):
        since = run.state.get("since") or run.now
        if run.now - since < QUIET_MAX_DAYS * 86400:
            gs["quiet"] = True
            raise Wait("quiet", "Waiting to update this gallery while the client is picking")
    gs.pop("quiet", None)
    limits = G._read(host, f"{gid}/limits.txt")
    if not current:
        keep_prev(run, gid, host, page_now, scripts_now, limits)
        for n in sorted(G.HOST_SCRIPTS, key=G.upload_order):
            _put(G, host, gid, n, script_bytes(G, n))
        if limits is not None and b"proof=1" not in limits and page_now and b"async function keyProof" in page_now:
            _put(G, host, gid, "limits.txt", limits.rstrip(b"\n") + b"\nproof=1\n")
    if not hostcheck.door_closed(base, gid, G.HOST_SCRIPTS_SET, wait=p.get("wait", 2)):
        raise Wait("door", "its new scripts are not running yet (the host may keep the old ones a few minutes)")
    steps.after_door(run, r, host)
    hostfiles.relayout(host, gid)
    flags_up(G, host, gid)
    steps.before_page(run, r, host)
    if G._read(host, f"{gid}/index.html") != want:
        _put(G, host, gid, "index.html", want)
    if G._read(host, f"{gid}/index.html") != want:
        raise Wait("readback", "the page read back is not the one just put there")
    limits = G._read(host, f"{gid}/limits.txt")
    if limits is not None and b"proof=1" not in limits:
        _put(G, host, gid, "limits.txt", limits.rstrip(b"\n") + b"\nproof=1\n")
    _https_page(run, gid, base, want, gs)
    if not hs.get("canary_ok_fp") == run.fp and not gate_closed(G, host, gid, run.now):
        why = canary(run, r, host, base, want)
        if why:
            roll_back(run, gid, host)
            tries = int(hs.get("canary_fails") or 0) + 1
            hs["canary_fails"] = tries
            if tries >= CANARY_TRIES:
                hs.update(failed_fp=run.fp, failed_words=why)
            raise Wait("canary", why)
        hs["canary_ok_fp"] = run.fp
        hs.pop("canary_fails", None)
    run.x("UPDATE galleries SET page_set=? WHERE session_id=?", (run.fp, r["sid"]))
    steps.record(run, r, host)
    gs.pop("cache_since", None)
    gs.pop("why", None)
    steps.after(run, r, host)
    return "done"

def run_all(ctx, G, only_sid=None, force=False):

    q, x = ctx.q, ctx.x
    if not G.has_host():
        return 0
    fp = fingerprint(G)
    st = load_state(q)
    if st.get("fp") != fp:
        st = dict(fp=fp, since=time.time(), hosts={h: {k: v for k, v in d.items() if k in ("php", "version")}
                                                    for h, d in (st.get("hosts") or {}).items()},
                  galleries={}, now=list(st.get("now") or []))
    cur = G.host_fingerprint()
    b = q("SELECT value FROM settings WHERE key='gallery_base_url'", one=True)
    base = (b["value"] if b and b["value"] else "").strip()
    rows = q("""SELECT g.*, s.gallery_id, s.id AS sid, s.first_visit, s.retouch_picks, s.favorites, s.veto, s.stage
                FROM galleries g JOIN sessions s ON s.id=g.session_id
                WHERE g.removed_at IS NULL AND g.upload_state='up' AND g.sealed_gen=1
                AND (g.page_set IS NULL OR g.page_set != ?) AND (g.host_fp IS NULL OR g.host_fp=?)""", (fp, cur))

    rows = [r for r in rows if (G.is_link(r["gallery_id"] or "") or ("adopted" in r.keys() and r["adopted"]))
            and (not only_sid or r["sid"] == only_sid)]
    run = Run(G, q, x, ctx.audit, base, st)
    n, waiting = 0, False
    if rows:
        host = G.gallery_host()
        with host.session():
            for r in rows:
                gid = r["gallery_id"]
                try:
                    refit_gallery(run, r, host, cur, base, force=force or gid in (st.get("now") or []))
                    n += 1
                    st["now"] = [g for g in (st.get("now") or []) if g != gid]
                    run.gal_state(gid).pop("quiet", None)
                except Wait as w:
                    waiting = True
                    run.gal_state(gid)["why"] = w.why
                    if w.why in ("unreachable", "old-php", "canary", "door") + hostcheck.CERT_WAITS:
                        break
                save_state(x, st)
        if not waiting:
            ctx.hooks.fire("host_refit", cur)
    save_state(x, st)
    return n

def _https_page(run, gid, base, want, gs):
    st, body = hostcheck.fetch(hostcheck.join(base, f"{gid}/index.html"))
    if st == 200 and body == want:
        gs.pop("cache_since", None)
        return
    since = gs.setdefault("cache_since", run.now)
    if run.now - since >= CACHE_GIVE_UP_HOURS * 3600:
        gs["cache_stuck"] = True
    raise Wait("cache", "A cache in front of your host still serves the old gallery page" if gs.get("cache_stuck") else
               "the new page is not served at its address yet")

def check_again(x, q, hfp):

    st = load_state(q)
    hs = (st.get("hosts") or {}).get(hfp)
    if hs is None:
        return False
    for k in ("failed_fp", "failed_words", "canary_fails"):
        hs.pop(k, None)
    save_state(x, st)
    return True

def today_lines(G, q, label_of, retry=None):

    st = load_state(q)
    out = []
    quiet = [g for g, v in (st.get("galleries") or {}).items() if v.get("quiet")]
    if quiet:
        out.append(dict(text=f"{len(quiet)} galler{'y waits' if len(quiet) == 1 else 'ies wait'} to be updated while the "
                             "client is picking. If a client says 'Send' failed, ask them to reload the page; their "
                             "choices are still on their device."))
    for hfp, v in (st.get("hosts") or {}).items():
        if v.get("failed_fp") and v.get("failed_fp") == st.get("fp"):
            out.append(dict(text=f"The new gallery page failed its check on {label_of(hfp)}; galleries there stay on the "
                                 f"previous version ({v.get('failed_words', '')}).", **(retry(hfp) if retry else {})))
    if any(v.get("cache_stuck") for v in (st.get("galleries") or {}).values()):
        out.append(dict(text="A cache in front of your host still serves the old gallery page."))
    return out
