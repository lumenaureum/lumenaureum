import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_fixes_141 import _served_env

def _set(A, **kv):
    con = A.connect()
    for k, v in kv.items():
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    con.commit()
    con.close()

def test_the_gallery_header_and_print_pickup_name_the_brand_not_the_llc(tmp_path, monkeypatch):
    e, PE = _served_env(tmp_path, monkeypatch)
    _set(e.A, studio_name="Lark & Pine Studio", business_name="Lark & Pine LLC", photographer_name="Avery Marsh")
    with e.A.app.test_request_context():
        s = e.A.q("SELECT * FROM sessions WHERE id=1", one=True)
        assert e.A.app.config["gallery_brand"](s)["studio"] == "Lark & Pine Studio"
    _set(e.A, studio_name="")
    with e.A.app.test_request_context():
        s = e.A.q("SELECT * FROM sessions WHERE id=1", one=True)
        assert e.A.app.config["gallery_brand"](s)["studio"] == "Lark & Pine LLC"

def test_print_orders_pickup_reads_the_brand_first():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins", "prints.py")).read()
    i = src.index("    def studio_name():")
    body = src[i:src.index("\n\n", i)]
    assert body.index('"studio_name"') < body.index('"business_name"')
