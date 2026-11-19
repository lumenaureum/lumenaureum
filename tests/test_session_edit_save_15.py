import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_client_page_141 import E, _session, env

def test_the_edit_form_posts_where_saving_happens(env):
    A, c = env
    _session(A, "booked", days=10)
    page = c.get("/session/1/edit", environ_base=E).get_data(as_text=True)
    actions = re.findall(r'<form method="post" action="([^"]+)"', page)
    assert "/session/1/edit" not in actions
    assert "/session/1" in actions
    for a in set(actions):
        assert c.post(a, data={}, environ_base=E).status_code != 405, a
