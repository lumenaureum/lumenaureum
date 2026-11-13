"""In-process locks (one app process, several threads): one per session for the long jobs that must take turns
(sealing, purging, gallery builds, uploads), one per uploaded file, and one for every saving request."""
import threading

_GUARD = threading.Lock()
_SESSIONS, _FILES = {}, {}
SEALING = set()                  # session ids whose photos are being sealed or deleted right now (uploads wait)
WRITES = threading.RLock()       # every saving request (POST) takes this, so two never interleave their changes
                                 # (re-entrant: a job run inline in tests takes it inside the request that started it)


def session(sid):
    with _GUARD:
        return _SESSIONS.setdefault(int(sid), threading.Lock())


def photo(fid):
    with _GUARD:
        return _FILES.setdefault(str(fid), threading.Lock())
