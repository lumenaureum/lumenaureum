import os
import sys
import threading
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import fresh_modules, signed_in_client

E = {"REMOTE_ADDR": "127.0.0.1"}

def make_studio(tmp_path, monkeypatch):
    for k, v in (("CRM_DB", "crm.db"), ("CRM_STORAGE_CFG", "storage.json"), ("CRM_PHOTOS_ROOT", "photos"),
                 ("CRM_GALLERY_ROOT", "galleries")):
        monkeypatch.setenv(k, str(tmp_path / v))
    monkeypatch.setenv("CRM_PHOTO_FOLDERS", "1")
    fresh_modules()
    import app as A
    c = signed_in_client(A)
    c.post("/setup/shoot", data={"preset_families": "1"}, environ_base=E)
    from vault import web as VW
    host = tmp_path / "host"
    host.mkdir()
    (host / ".vault-root").write_text("")
    cfg = VW.config()
    cfg["gallery_host"] = {"type": "local", "path": str(host)}
    VW.write_config(cfg)
    con = A.connect()
    con.execute("INSERT INTO settings (key, value) VALUES ('gallery_base_url', 'https://gallery.test/g/')")
    con.execute("INSERT INTO households (name, address) VALUES ('The Hollis Family', '12 Elm St, Springfield')")
    con.execute("INSERT INTO people (household_id, name, role, email, phone) VALUES (1, 'Dana Hollis', 'adult', 'dana@example.com', '555-0100')")
    con.commit()
    con.close()
    return A, c

def sql(A, query, args=()):
    con = A.connect()
    try:
        cur = con.execute(query, args)
        con.commit()
        return cur
    finally:
        con.close()

def one(A, query, args=()):
    con = A.connect()
    try:
        return con.execute(query, args).fetchone()
    finally:
        con.close()

def day(n):
    return (date.today() + timedelta(days=n)).isoformat()

def new_session(A, type_="family", stage="inquiry", **cols):

    with A.app.test_request_context():
        A.db()
        sid = A.create_session(1, type_, stage="inquiry", shoot_date=cols.pop("shoot_date", None))
        A.g.db.commit()
    for k_, v_ in cols.items():
        sql(A, f"UPDATE sessions SET {k_}=? WHERE id=?", (v_, sid))
    if stage != "inquiry":
        set_stage(A, sid, stage)
    return sid

def set_stage(A, sid, stage):
    with A.app.test_request_context():
        A.db()
        A.set_stage(sid, stage)
        A.sync_workflow_tasks(sid)
        A.g.db.commit()

def run(A, fn):
    with A.app.test_request_context():
        A.db()
        out = fn()
        A.g.db.commit()
        return out

def join(prefix, sid):
    for t in [t for t in threading.enumerate() if t.name == f"{prefix}-{sid}"]:
        t.join(timeout=60)
        assert not t.is_alive()

def jpeg(color="white"):
    import io
    from PIL import Image
    b_ = io.BytesIO()
    Image.new("RGB", (200, 150), color).save(b_, "JPEG")
    return b_.getvalue()
