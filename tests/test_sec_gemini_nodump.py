import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import fresh_modules

def _status_dumpable_and_core(code):

    probe = code + """
import ctypes, ctypes.util, resource
libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
print(*resource.getrlimit(resource.RLIMIT_CORE), libc.prctl(3, 0, 0, 0, 0))
"""
    r = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT), capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONPATH=str(ROOT)))
    assert r.returncode == 0, r.stderr
    return tuple(int(x) for x in r.stdout.split()[-3:])

@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl is Linux only")
def test_harden_sets_no_core_and_not_dumpable():
    before = _status_dumpable_and_core("")
    assert before[2] == 1
    soft, hard, dumpable = _status_dumpable_and_core("import nodump; assert nodump.harden() == (True, True)")
    assert (soft, hard, dumpable) == (0, 0, 0)

@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl is Linux only")
def test_the_limit_carries_across_exec_and_the_app_marks_itself_again():
    code = ("import nodump, os, sys; nodump.harden(); "
            "os.execv(sys.executable, [sys.executable, '-c', "
            "'import ctypes, resource; print(*resource.getrlimit(resource.RLIMIT_CORE), "
            "ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))'])")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONPATH=str(ROOT)))
    soft, hard, dumpable = (int(x) for x in r.stdout.split())
    assert (soft, hard) == (0, 0) and dumpable == 1
    src = (ROOT / "app.py").read_text()
    assert "import nodump\nnodump.harden()" in src
    assert src.index("nodump.harden()") < src.index("app = Flask(")

def test_serve_hardens_before_it_starts_gunicorn(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    fresh_modules()
    order = []
    import nodump

    class Started(Exception):
        pass

    def fake_execvp(file, args):
        order.append("exec")
        raise Started()
    monkeypatch.setattr(nodump, "harden", lambda: order.append("harden") or (True, True))
    monkeypatch.setattr(os, "execvp", fake_execvp)
    with pytest.raises(Started):
        runpy.run_path(str(ROOT / "serve.py"), run_name="__main__")
    assert order == ["harden", "exec"]

def test_compose_sets_no_core_dumps():
    import re
    text = (ROOT / "compose.yaml").read_text()
    assert re.search(r"^    ulimits:\n      core: 0\b", text, re.M)
