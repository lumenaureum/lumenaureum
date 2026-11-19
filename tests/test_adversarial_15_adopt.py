import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_adopt_gallery_b3 import BASE, E, GID, adopt, env

def test_m3_a_refused_adoption_keeps_the_other_clients_name_out_of_history_and_export(env):
    A, c, G, host, key, photos, m = env
    con = A.connect()
    con.execute("INSERT INTO households (name) VALUES ('Secret Other Family')")
    con.execute("INSERT INTO sessions (household_id, type, stage, shoot_date) VALUES (2, 'family', 'shot', ?)", (date.today().isoformat(),))
    con.commit()
    con.close()
    owner = A.connect().execute("SELECT name FROM households WHERE id=1").fetchone()[0]
    assert adopt(c, f"{BASE}{GID}/#k={key}").status_code == 302
    c.post("/session/2/gallery/adopt", data={"link": f"{BASE}{GID}/#k={key}", "back": "/c/2?s=2"}, environ_base=E)
    with c.session_transaction() as s_:
        assert owner in (s_.get("notice") or "")
    con = A.connect()
    rows = [r[0] for r in con.execute("SELECT action FROM audit WHERE household_id=2").fetchall()]
    con.close()
    assert any(r.startswith("Adopting a gallery refused: that gallery already belongs to another session") for r in rows)
    assert not [r for r in rows if owner in r]
    assert owner not in c.get("/client/2/export.json", environ_base=E).get_data(as_text=True)
