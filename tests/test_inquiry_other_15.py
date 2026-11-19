import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_studio_inquiries import GOOD, put_inquiry
from test_studio_page import E, env, setting, switch_on, wait

ASSET = os.path.join(os.path.dirname(__file__), "..", "plugins", "studio_assets", "inquiry.js")

def test_other_is_always_offered_and_arrives_marked(env):
    A, c, S, tmp, web = env
    switch_on(c)
    c.post("/settings/studio-page/upload", environ_base=E)
    wait(S)
    contact = (web / "contact" / "index.html").read_text()
    assert '<option value="other">Other (explain below)</option></select>' in contact
    assert 'id="inq-intro"' in contact
    pub = S.inquiry_public(setting(A, "site_inquiry_key"))
    put_inquiry(web, S.seal_inquiry(pub, dict(GOOD, type="other", message="A pet portrait?")))
    assert A.app.config["studio_page_collect"]()["added"] == 1
    con = A.connect()
    rid = str(con.execute("SELECT id FROM studio_inquiry_review").fetchone()[0])
    con.close()
    assert "Other" in c.get("/", environ_base=E).get_data(as_text=True)
    c.post("/settings/studio-page/inquiries/review", data={"do": "add", "id": rid}, environ_base=E)
    con = A.connect()
    log = con.execute("SELECT summary FROM log ORDER BY id DESC").fetchone()[0]
    con.close()
    assert log.startswith("Inquiry from your website (something else: see their message)") and "A pet portrait?" in log

def test_sent_hides_the_invitation_to_ask():
    js = open(ASSET).read()
    i = js.index("f.hidden = true;\n      var intro")
    assert 'getElementById("inq-intro")' in js[i:i + 200] and "intro.hidden = true" in js[i:i + 200]
