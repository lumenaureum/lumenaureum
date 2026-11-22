import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_client_page_141 import E, env
from test_lists_15 import _clients_studio, _item, _rows
from test_today_15 import _x

def _render(A, src):
    with A.app.test_request_context():
        return A.app.jinja_env.from_string('{% import "_ui.html" as ui with context %}' + src).render()

def test_rule_14_the_fold_shows_its_count_in_its_title(env):
    A, c = env
    three = "{{ ui.fact('A', '1') }}{{ ui.fact('B', '2') }}{{ ui.fact('C', '3') }}"
    html = _render(A, "{% call ui.fold('Next two weeks', id='n', count=8) %}" + three + "{% endcall %}")
    assert '<span class="ui-fold-sum">Next two weeks (8)</span>' in html
    assert '<span class="ui-fold-sum">People</span>' in _render(A, "{% call ui.fold('People', id='p') %}" + three + "{% endcall %}")
    assert "Account activity (0)" in _render(A, "{% call ui.fold('Account activity', id='a', always=true, count=0) %}x{% endcall %}")

def test_rule_14_the_list_folds_pass_their_count(env):

    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")
    want = {"today.html": ["'Waiting on the client or nothing to do now'"],
            "client_stage.html": ["'Details and history'", "'History'", "'People'"],
            "money.html": ["'By session type'", "'Sessions started this year'"],
            "pipeline.html": ["stage_label[st]"], "auth_security.html": ["'Account activity'"],
            "setup.html": ["'Addresses that can connect'"]}
    for name, folds in want.items():
        src = open(os.path.join(root, name)).read()
        for f_ in folds:
            calls = re.findall(r"ui\.fold\(" + re.escape(f_) + r"[^%]*%\}", src)
            assert calls and all("count=" in c_ for c_ in calls), (name, f_)

def test_clients_owes_and_to_invoice_are_two_sortable_columns(env):
    A, c = env
    _clients_studio(A)

    _x(A, "INSERT INTO lines (session_id, label, amount, kind) VALUES (12, 'Family session', 240, 'fee')")
    page = c.get("/clients", environ_base=E).get_data(as_text=True)
    heads = re.findall(r'data-r-sort="(\w+)"[^>]*>([^<]+)</a>', page)
    assert heads[-2:] == [("owed", "Owes"), ("toinv", "To invoice")]
    assert 'data-r-sort="toinv" data-r-num="1"' in page
    realty, ellis = _item(page, 5), _item(page, 2)
    assert '<span class="ui-late" data-tip="Overdue">$318.00</span>' in realty
    assert '<td class="ui-roster-toinv">$240.00</td>' in ellis and '<td class="ui-roster-owed"></td>' in ellis
    assert _rows(c.get("/clients?sort=toinv&dir=desc", environ_base=E).get_data(as_text=True))[0] == "2"
    assert _rows(c.get("/clients?sort=owed&dir=desc", environ_base=E).get_data(as_text=True))[0] == "5"
