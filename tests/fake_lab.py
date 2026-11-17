import time

def make(ctx_prints, lab_id="testlab", fail=None, hang=None):

    fake = dict(calls=[], queue={}, placed={}, refs={}, snapshots=[])
    fail, hang = set(fail or ()), set(hang or ())

    def guard(name):
        fake["calls"].append(name)
        if name in fail:
            raise RuntimeError(f"the fake lab broke in {name}")
        if name in hang:
            time.sleep(5)

    def ready():
        guard("ready")
        return True, "Test lab: nothing is printed"

    def item_fields(item):
        guard("item_fields")
        return f'<label>Fake product <input name="fake_{item["id"]}" value="{fake["refs"].get(item["id"], "")}"></label>'

    def item_save(item, form):
        guard("item_save")
        ref = form.get("fake_" + item["id"])
        if ref:
            fake["refs"][item["id"]] = ref
            return {"product": ref}, ""
        return None, ""

    def starter_items():
        guard("starter_items")
        return [dict(label="5x7 print, Matte", w=5, h=7, finish="Matte", each=2500, cost=300, lab={lab_id: {"product": "P57"}}),
                dict(label="8x10 print, Matte", w=8, h=10, finish="Matte", each=4000, cost=500, lab={lab_id: {"product": "P810"}})]

    def costs(items):
        guard("costs")
        return {i["id"]: dict(cents=111 * (n + 1), as_of="2026-11-16", words="test lab") for n, i in enumerate(items)}

    def shipping_quote(order):
        guard("shipping_quote")
        fake["snapshots"].append(order)
        return dict(cents=912, lab_tax_cents=0, total_cost_cents=0, words="fake ground")

    def can_send(order):
        guard("can_send")
        fake["snapshots"].append(order)
        return True, []

    def send(order):
        guard("send")
        fake["snapshots"].append(order)
        fake["queue"][order["id"]] = "queued"
        return dict(accepted=True, words="goes to the test lab at 3:00 pm")

    def status(order):
        guard("status")
        return dict(words=f"test lab: {fake['queue'].get(order['id'], 'unknown')}")

    def cancel(order, confirm=False):
        guard("cancel")
        if not confirm:
            return dict(outcome="not_placed", words="it is still queued: nothing at the lab")
        fake["queue"].pop(order["id"], None)
        return dict(outcome="not_placed", words="removed from the queue")

    lab = dict(id=lab_id, name="Test Lab", ready=ready, item_fields=item_fields, item_save=item_save,
               starter_items=starter_items, costs=costs, shipping_quote=shipping_quote, can_send=can_send, send=send,
               status=status, cancel=cancel, file_rules=dict(max_bytes=40_000_000, quality=95, color="sRGB"),
               host_folder="testlab.d")
    return lab, fake
