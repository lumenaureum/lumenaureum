import contextlib
import hashlib
import io
import re

EXIT = b"<?php exit; ?>\n"
GATED = {"manifest.bin": ".bin", "offer.bin": ".bin", "picks.las": ".las", "draft.las": ".las"}
OWNED = ("manifest.bin", "offer.bin")
STATE = {"limits.txt": "limits.php", "seen.txt": "seen-at.php", "once.txt": "once.php", "done.txt": "done.php"}
STATE_BACK = {v: k for k, v in STATE.items()}
PROOF_OK = re.compile(r"[0-9a-f]{32}")

def proof_of(key_raw):

    return hashlib.sha256(key_raw).hexdigest()[:32]

def name_for(proof, plain):

    if plain not in GATED or not PROOF_OK.fullmatch(proof or ""):
        raise ValueError("not a gated file or not a proof")
    h = hashlib.sha256(b"lumen-aureum file name\0" + plain.encode() + b"\0" + proof.encode()).hexdigest()
    return h[:24] + GATED[plain]

def state_bytes(raw):

    return raw[len(EXIT):] if raw.startswith(EXIT) else raw

class HostFiles:

    def __init__(self, backend, proof):
        self._b = backend
        self._proof = proof

    def __getattr__(self, name):
        return getattr(self._b, name)

    @property
    def backend(self):
        return self._b

    @contextlib.contextmanager
    def session(self):
        with self._b.session():
            yield self

    def _split(self, name):
        if name.count("/") != 1:
            return None, None
        return name.split("/", 1)

    def _gated(self, name):

        folder, base = self._split(name)
        if base not in GATED:
            return None
        try:
            p = self._proof(folder)
        except Exception:
            p = None
        if not p or not PROOF_OK.fullmatch(p):
            return None
        return f"{folder}/{name_for(p, base)}"

    def _state(self, name):
        folder, base = self._split(name)
        return f"{folder}/{STATE[base]}" if base in STATE else None

    def put_stream(self, name, reader, content_type=None, cache_control=None):
        st = self._state(name)
        if st:
            out = self._b.put_stream(st, io.BytesIO(EXIT + reader.read()), content_type=content_type,
                                     cache_control=cache_control)
            if self._b.stat(name) is not None:
                self._b.delete(name)
            return out
        g = self._gated(name)
        if g:
            out = self._b.put_stream(g, reader, content_type=content_type, cache_control=cache_control)
            if name.rsplit("/", 1)[1] in OWNED and self._b.stat(name) is not None:
                self._b.delete(name)
            return out
        return self._b.put_stream(name, reader, content_type=content_type, cache_control=cache_control)

    def get_stream(self, name, writer):
        st = self._state(name)
        if st and self._b.stat(st) is not None:
            buf = io.BytesIO()
            self._b.get_stream(st, buf)
            writer.write(state_bytes(buf.getvalue()))
            return
        g = self._gated(name)
        if g and self._b.stat(g) is not None:
            return self._b.get_stream(g, writer)
        return self._b.get_stream(name, writer)

    def stat(self, name):
        st = self._state(name)
        if st:
            n = self._b.stat(st)
            if n is not None:
                return max(0, n - len(EXIT))
        g = self._gated(name)
        if g:
            n = self._b.stat(g)
            if n is not None:
                return n
        return self._b.stat(name)

    def delete(self, name):
        for other in (self._state(name), self._gated(name)):
            if other:
                self._b.delete(other)
        return self._b.delete(name)

    def delete_verified(self, name):
        self.delete(name)
        if self.stat(name) is not None:
            raise IOError(f"{getattr(self._b, 'kind', 'host')}: {name} still present after delete")

    def list(self, prefix=""):
        out, back = [], {}
        for n in self._b.list(prefix):
            folder, base = self._split(n)
            if base is None:
                out.append(n)
                continue
            if base in STATE_BACK:
                n = f"{folder}/{STATE_BACK[base]}"
            else:
                if folder not in back:
                    try:
                        p = self._proof(folder)
                    except Exception:
                        p = None
                    back[folder] = {name_for(p, k): k for k in GATED} if p and PROOF_OK.fullmatch(p) else {}
                if base in back[folder]:
                    n = f"{folder}/{back[folder][base]}"
            out.append(n)
        return sorted(dict.fromkeys(out))

    def plain_copy(self, name):

        return self._b.stat(name) if (self._gated(name) or self._state(name)) else None

def unwrap(host):

    return host.backend if isinstance(host, HostFiles) else host

def _raw(b, name):
    if b.stat(name) is None:
        return None
    buf = io.BytesIO()
    b.get_stream(name, buf)
    return buf.getvalue()

def relayout(host, folder, remove=True):

    if not isinstance(host, HostFiles):
        return []
    b, done = host.backend, []
    for plain in list(GATED) + list(STATE):
        old = f"{folder}/{plain}"
        new = host._gated(old) or host._state(old)
        if not new:
            continue
        blob = _raw(b, old)
        if blob is None:
            continue
        if b.stat(new) is None:
            data = (EXIT + blob) if plain in STATE else blob
            b.put_stream(new, io.BytesIO(data), content_type="application/octet-stream", cache_control="no-cache")
            if _raw(b, new) != data:
                continue
        if not remove:
            continue
        now = _raw(b, old)
        if now is not None and hashlib.sha256(now).digest() == hashlib.sha256(blob).digest():
            b.delete(old)
            done.append(plain)
    return done

def unlayout(host, folder):

    if not isinstance(host, HostFiles):
        return []
    b, done = host.backend, []
    for plain in list(GATED) + list(STATE):
        old = f"{folder}/{plain}"
        new = host._gated(old) or host._state(old)
        if not new:
            continue
        blob = _raw(b, new)
        if blob is None:
            continue
        data = state_bytes(blob) if plain in STATE else blob
        b.put_stream(old, io.BytesIO(data), content_type="application/octet-stream", cache_control="no-cache")
        if _raw(b, old) != data:
            continue
        now = _raw(b, new)
        if now is not None and hashlib.sha256(now).digest() == hashlib.sha256(blob).digest():
            b.delete(new)
            done.append(plain)
    return done
