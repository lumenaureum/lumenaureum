import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def example_code():
    text = open(os.path.join(ROOT, "plugins", "README.md")).read()
    return re.search(r"`my-plugins/thank_you.py`.*?```python\n(.*?)```", text, re.S).group(1)

def test_readme_example_plugin(tmp_path, monkeypatch):
    mine = tmp_path / "my-plugins"
    mine.mkdir()
    (mine / "thank_you.py").write_text(example_code())
    (mine / "_draft.py").write_text("raise SystemExit('drafts must not load')")
    (mine / "zz_note.py").write_text("def register(ctx):\n    ctx.hooks.today.append(lambda: [dict(text='Renew the studio lease', link='/settings')])\n")
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PLUGIN_DIRS", str(mine))
    fresh_modules()
    import app as A
    assert "thank_you" in A.LOADED
    c = signed_in_client(A)
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Harlow Family')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (1, 'family', 'delivered', '2026-01-05')")
    con.commit(); con.close()
    page = c.get("/", environ_base=E).get_data(as_text=True)
    assert "Send Harlow Family a thank-you card" in page and "Renew the studio lease" in page
    assert "Mark card sent" in c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    c.post("/thank-you/1", environ_base=E)
    assert "Card sent" in c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    assert "Send Harlow Family a thank-you card" not in c.get("/", environ_base=E).get_data(as_text=True)

def test_plugins_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    monkeypatch.setenv("CRM_PLUGINS_OFF", "letters")
    fresh_modules()
    import app as A
    assert "letters" not in A.LOADED and "gallery" in A.LOADED

def test_about_and_log_name_loaded_plugins(tmp_path, monkeypatch):

    monkeypatch.setenv("CRM_DB", str(tmp_path / "crm.db"))
    monkeypatch.setenv("CRM_STORAGE_CFG", str(tmp_path / "storage.json"))
    fresh_modules()
    import app as A
    import netsec
    assert A.PLUGINS_LOADED and all(p["built_in"] for p in A.PLUGINS_LOADED)
    c = signed_in_client(A)
    page = c.get("/about", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "not part of Lumen Aureum" not in page
    A.PLUGINS_LOADED.append(dict(name="mine", built_in=False, folder="/plugins", label="plugins/mine"))
    page = c.get("/about", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "The following plugins are loaded and are not part of Lumen Aureum: plugins/mine." in page and "including client data" in page
    anon = A.app.test_client().get("/about", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "mine" not in anon
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        netsec.banner(False, plugins=[("galleries", True, "/app/plugins"), ("mine", False, "/plugins")])
    out = buf.getvalue()
    flat = " ".join(ln.strip() for ln in out.splitlines())
    assert "*** WARNING *** : The following plugins are loaded and are not part of Lumen Aureum: plugins/mine." in flat and "galleries" not in out
    lines = out.splitlines()
    i = next(n for n, ln in enumerate(lines) if "WARNING" in ln)
    j = next(n for n, ln in enumerate(lines) if "client data." in ln)
    assert all(len(ln) <= 66 for ln in lines[i:j + 1])
