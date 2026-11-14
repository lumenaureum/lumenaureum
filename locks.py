import threading

_GUARD = threading.Lock()
_SESSIONS, _FILES = {}, {}
SEALING = set()
WRITES = threading.RLock()

def session(sid):
    with _GUARD:
        return _SESSIONS.setdefault(int(sid), threading.Lock())

def photo(fid):
    with _GUARD:
        return _FILES.setdefault(str(fid), threading.Lock())
