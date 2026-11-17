import ctypes
import ctypes.util
import resource
import sys

PR_SET_DUMPABLE = 4

def harden():

    core = dumpable = False
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        core = True
    except (ValueError, OSError):
        pass
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
            dumpable = libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
        except (OSError, AttributeError):
            pass
    return core, dumpable
