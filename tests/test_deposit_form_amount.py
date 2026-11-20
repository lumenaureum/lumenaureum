import re

from test_stage_logic_150 import E, _invoice_sent, _price, _session, env

def test_the_deposit_box_holds_the_deposit_its_button_names(env):
    A, c = env
    _session(A, "booked", days=60, logged=[("booked", 0)])
    _price(A, c, "2800")
    _invoice_sent(A, c)
    with A.app.app_context():
        A.sync_workflow_tasks(1)
    page = c.get("/c/1?s=1", environ_base=E).get_data(as_text=True)
    verbs = re.findall(r"Record payment \((\$[\d,.]+)\)", page)
    assert "$100.00" in verbs, verbs
    forms = re.findall(r"<form[^>]*>(?:(?!</form>).)*?</form>", page, re.S)
    pay = [f for f in forms if 'name="what" value="payment"' in f]
    amounts = [(re.search(r'name="kind" value="(\w+)"', f).group(1), re.search(r'name="amount"[^>]*value="([\d.]+)"', f).group(1))
               for f in pay if re.search(r'name="amount"[^>]*value="([\d.]+)"', f)]
    assert amounts and all(a == "100.00" for _k, a in amounts), amounts
