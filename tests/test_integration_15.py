import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_stage_logic_150 import E, _acts, _day, _invoice_sent, _price, _session, _sql, env

def _today(c):
    r = c.get("/", environ_base=E)
    assert r.status_code == 200
    return r.get_data(as_text=True)

def _line(page, sid=1):
    m = re.search(r'<li class="ui-tline[^"]*" id="line-s%d"[^>]*>(.*?)</li>' % sid, page, re.S)
    assert m, "no Today line for session %d" % sid
    return m.group(1)

def test_an_inquiry_replies_with_the_mail_action(env):
    A, c = env
    _session(A, "inquiry", days=None)
    _sql(A, "INSERT INTO log (household_id, session_id, channel, summary, at) VALUES (1, 1, 'email', 'Any dates in May?', ?)",
         (A.today().isoformat() + " 09:00:00",))
    line = _line(_today(c))
    assert 'action="/session/1/reply"' in line and 'data-ui-mail="mailto:alex@example.com' in line and ">Reply</button>" in line

    r = c.post("/session/1/reply", data={"mail": "1"}, environ_base=E)
    assert r.status_code == 303 and r.headers["Location"].startswith("mailto:alex@example.com")

def test_a_quote_waiting_is_booked_with_the_same_post_as_the_client_page(env):
    A, c = env
    _session(A, "inquiry", days=30)
    _price(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    vid = _sql(A, "SELECT id FROM quote_versions WHERE session_id=1")[0][0]
    c.post(f"/quote/{vid}/sent", environ_base=E)
    assert _sql(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "quoted"
    line = _line(_today(c))
    assert ">Book it</button>" in line and 'name="act" value="book"' in line

def test_a_quote_unanswered_five_days_follows_up_with_the_mail_action(env):
    A, c = env
    _session(A, "quoted", days=30)
    _price(A, c)
    c.post("/session/1/quote/send", environ_base=E)
    vid, qno = _sql(A, "SELECT id, number FROM quote_versions WHERE session_id=1")[0]
    assert qno == A.today().strftime("%Y%m%d") + "01"
    _sql(A, "INSERT INTO quote_marks (vid, session_id, at) VALUES (?, 1, ?)", (vid, _day(A, -6)))
    _sql(A, "DELETE FROM log")
    line = _line(_today(c))
    assert 'action="/session/1/followup"' in line and 'data-ui-mail="mailto:alex@example.com?subject=Following' in line
    assert ">Follow up</button>" in line and f'name="vid" value="{vid}"' in line and f"quote {qno}" in line

    r = c.post("/session/1/followup", data={"vid": str(vid), "mail": "1"}, environ_base=E)
    assert r.status_code == 303 and r.headers["Location"].startswith("mailto:alex@example.com")
    assert _sql(A, "SELECT summary FROM log WHERE session_id=1")[-1][0] == f"Followed up on quote {qno}"
    assert 'action="/session/1/followup"' not in _line(_today(c))

def test_a_shoot_past_its_day_is_shoot_done_with_the_stage_rule_post(env):
    A, c = env
    _session(A, "booked", days=-2, start="10:00")
    line = _line(_today(c))
    assert ">Shoot done</button>" in line and 'name="act" value="shot"' in line
    c.post("/session/1/quick", data={"act": "shot"}, environ_base=E)
    assert _sql(A, "SELECT stage FROM sessions WHERE id=1")[0][0] == "shot"

def test_a_tentative_date_is_never_shoot_done(env):
    A, c = env
    _session(A, "booked", days=-2, start="10:00")
    _sql(A, "UPDATE sessions SET date_tentative=1 WHERE id=1")
    assert ">Shoot done</button>" not in _line(_today(c))

def test_delivery_while_owed_reads_collect_first_on_today(env):
    A, c = env
    _session(A, "delivered", days=-20)
    _price(A, c, "318")
    page = _today(c)

    assert "Invoice ($318.00 balance, Family session)" in page and ">Send the invoice<" in page and ">Mark delivered<" not in page
    _invoice_sent(A, c)
    page = _today(c)
    assert "Collect the $318.00 balance first" in page and ">Mark delivered<" not in page
    assert ">Record payment ($318.00)<" in page

def test_the_update_note_names_the_sessions_the_stage_rules_moved(env):
    A, c = env
    words = "Moved on under the new stage rules: Reyes. Each one's History says what changed."
    _sql(A, "INSERT OR REPLACE INTO settings (key, value) VALUES ('update_notice', ?)",
         (json.dumps(dict(version="1.5.0", backup=True, changed=True, stages=words)),))
    page = _today(c)
    assert "Updated to Lumen Aureum 1.5.0." in page and words.replace("'", "&#39;") in page
    assert words.replace("'", "&#39;") in _today(c)
    c.post("/today/update-notice-hide", data={"back": "/"}, environ_base=E)
    assert words.replace("'", "&#39;") not in _today(c)

def test_no_link_to_a_part_of_the_old_session_page(env):

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = []
    for d_ in ("", "plugins", "templates"):
        for f_ in os.listdir(os.path.join(root, d_)):
            if f_.endswith((".py", ".html")):
                text = open(os.path.join(root, d_, f_), encoding="utf-8").read()
                bad += [f"{d_}/{f_}: {m}" for m in re.findall(r"session_view[^\n]{0,60}#\w+", text)]
    assert not bad, bad
