import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_user_testing_fixes import E, _app, _new, _q

def _form(C, key, **on):

    f = {"notice_hours": "48", "late_minutes": "30", "free_reschedule": "1", "outfits": "1"}
    for stage, rows in C.type_steps_all(next(t for t in C.TYPES if t["key"] == key)).items():
        for i, (st, off) in enumerate(rows):
            k = f"{stage}_{i}"
            f[k + "_kind"] = st[0]
            if st[0] == "auto":
                f[k + "_key"] = st[1]
            else:
                f[k + "_label"] = st[1]
                f[k + "_days"] = "" if st[2] is None else str(st[2])
            f[k + "_on"] = on.get(k, "0" if off else "1")
    return f

def _type(C, key):
    return next(t for t in C.TYPES if t["key"] == key)

def _idx(C, key, stage, sid_):
    return [C.step_id(st) for st, _ in C.type_steps_all(_type(C, key))[stage]].index(sid_)

def test_a_step_turned_off_stays_in_its_place_and_the_workflow_treats_it_as_absent(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families",))
    import config as C
    key = "family"
    sid = _new(A, c, key)
    i = _idx(C, key, "booked", ("do", "Send session details"))
    assert _q(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send session details'", (sid,))
    c.post(f"/settings/type/{key}", data=_form(C, key, **{f"booked_{i}": "0"}), environ_base=E)
    t = _type(C, key)
    assert "steps" not in t
    assert t["steps_off"] == {"booked": [["do", "Send session details"]]}
    assert ("do", "Send session details", -7) not in C.WORKFLOW[key]["booked"]
    assert C.type_steps_all(t)["booked"][i] == (("do", "Send session details", -7), True)
    assert not _q(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send session details'", (sid,))
    page = c.get(f"/settings/type/{key}", environ_base=E).get_data(as_text=True)
    assert 'class="step-off"' in page and f'name="booked_{i}_on" value="0"' in page and ">Turn on<" in page
    c.post(f"/settings/type/{key}", data=_form(C, key, **{f"booked_{i}": "1"}), environ_base=E)
    t = _type(C, key)
    assert "steps_off" not in t and "steps" not in t
    assert C.WORKFLOW[key]["booked"][i] == ("do", "Send session details", -7)
    assert _q(A, "SELECT 1 FROM tasks WHERE session_id=? AND title='Send session details'", (sid,))

def test_step_lists_from_drop_and_forms_without_the_switch_keep_working(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families",))
    import config as C
    key = "family"
    f = _form(C, key)
    for k_ in [k_ for k_ in f if k_.endswith("_on")]:
        del f[k_]
    i = _idx(C, key, "booked", ("do", "Confirm time and place"))
    f[f"booked_{i}_drop"] = "1"
    c.post(f"/settings/type/{key}", data=f, environ_base=E)
    t = _type(C, key)
    assert "steps_off" not in t
    assert ("do", "Confirm time and place", -1) not in C.WORKFLOW[key]["booked"]
    assert all(not off for st, off in C.type_steps_all(t)["booked"])
    assert len(t["steps"]["booked"]) == len(C.KINDS["portrait"]["flow"]["booked"]) - 1

    i2 = _idx(C, key, "booked", ("auto", "releases_signed"))
    c.post(f"/settings/type/{key}", data=_form(C, key, **{f"booked_{i2}": "0"}), environ_base=E)
    data = c.get("/settings/export.json", environ_base=E).get_json()
    exp = next(t_ for t_ in data["rules"]["TYPES"] if t_["key"] == key)
    assert exp["steps_off"] == {"booked": [["auto", "releases_signed"]]}
    import settings_io
    back = settings_io._types([exp])[0]
    assert back["steps_off"] == exp["steps_off"]

def test_reset_steps_to_the_defaults(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families",))
    import config as C
    key = "family"
    f = _form(C, key, **{f"booked_{_idx(C, key, 'booked', ('auto', 'releases_signed'))}": "0"})
    f["shot_new"] = "Charge batteries"
    c.post(f"/settings/type/{key}", data=f, environ_base=E)
    assert _type(C, key).get("steps") and _type(C, key).get("steps_off")
    page = c.get(f"/settings/type/{key}", environ_base=E).get_data(as_text=True)
    assert "Reset steps to the defaults" in page and "back to the defaults?" in page
    c.post(f"/settings/type/{key}/steps-reset", environ_base=E)
    t = _type(C, key)
    assert "steps" not in t and "steps_off" not in t
    assert C.WORKFLOW[key] == {k: list(v) for k, v in C.KINDS["portrait"]["flow"].items()}

def test_the_gallery_step_cannot_be_turned_off_and_the_money_steps_ask_first(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families",))
    import config as C
    key = "family"
    g = _idx(C, key, "shot", ("auto", "gallery_built"))
    f = _form(C, key, **{f"shot_{g}": "0"})
    c.post(f"/settings/type/{key}", data=f, environ_base=E)
    assert ("auto", "gallery_built") in C.WORKFLOW[key]["shot"]
    f = _form(C, key)
    f[f"shot_{g}_drop"] = "1"
    c.post(f"/settings/type/{key}", data=f, environ_base=E)
    assert ("auto", "gallery_built") in C.WORKFLOW[key]["shot"]
    page = c.get(f"/settings/type/{key}", environ_base=E).get_data(as_text=True)
    assert "The Gallery panel needs this step." in page and f'name="shot_{g}_on"' not in page
    assert 'data-warn="Sessions of this type will move past Booked without a signed agreement."' in page
    assert "Sessions of this type will move past Booked without a deposit." in page
    assert "without waiting for the client to say yes" in page
    assert 'data-stage-warn="Every step of Booked is off.' in page

    i = _idx(C, key, "booked", ("auto", "agreement_signed"))
    c.post(f"/settings/type/{key}", data=_form(C, key, **{f"booked_{i}": "0"}), environ_base=E)
    assert ("auto", "agreement_signed") not in C.WORKFLOW[key]["booked"]

    f = _form(C, key, **{f"delivered_{j}": "0" for j in range(len(C.type_steps_all(_type(C, key))["delivered"]))})
    c.post(f"/settings/type/{key}", data=f, environ_base=E)
    assert C.WORKFLOW[key]["delivered"] == []

def test_another_types_answer_is_offered_under_each_field(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("portraits", "families", "weddings"))
    import config as C
    import walkthrough as W
    w = W.reuse("wedding", "weather")
    assert w and w[0]["words"] == "Portraits and Family: we reschedule at no charge"
    assert w[0]["fill"] == {"weather": "we reschedule at no charge"}
    assert W._names(["A", "B", "C"]) == "A, B, and C" and W._names(list("ABCDE")) == "A, B, C, and 2 more"
    assert not [x for x in W.reuse("portrait", "weather") if x["words"].startswith("Family")]
    assert [x["words"] for x in W.reuse("portrait", "reschedule")] == [
        "Weddings: no free reschedule, no no-show rule"]
    assert W.reuse("portrait", "delivery_days")[0]["fill"] == {"delivery_days": 60}
    page = c.get("/settings/type/wedding", environ_base=E).get_data(as_text=True)
    assert "Match Portraits and Family: we reschedule at no charge" in page
    assert 'data-fill="{&#34;weather&#34;: &#34;we reschedule at no charge&#34;}"' in page

    types = [json.loads(json.dumps(t)) for t in C.TYPES]
    next(t for t in types if t["key"] == "portrait").setdefault("policy", {})["weather"] = ""
    with A.app.test_request_context():
        A.db()
        A._save_rules(TYPES=types)
        A._commit()
    assert [x["words"] for x in W.reuse("wedding", "weather")] == ["Family: we reschedule at no charge"]
    ids = [s["id"] for s in W.steps_for(dict(_type(C, "wedding")))]
    walk = c.get(f"/settings/type/wedding/walk/{ids.index('weather')}", environ_base=E).get_data(as_text=True)
    assert "Family: we reschedule at no charge" in walk and "data-fill=" in walk

def test_the_walk_through_has_a_steps_screen(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families",))
    import config as C
    import walkthrough as W
    key = "family"
    ids = [s["id"] for s in W.steps_for(dict(_type(C, key)))]
    n = ids.index("steps")
    assert n == 1
    page = c.get(f"/settings/type/{key}/walk/{n}", environ_base=E).get_data(as_text=True)
    assert "Your steps" in page and "Steps on" in page and "Turn off" in page
    assert "The Gallery panel needs this step." in page
    assert "Ticks itself once every agreement for the session is signed." in page
    assert 'data-warn="Sessions of this type will move past Booked without a signed agreement."' in page
    total = sum(len(v) for v in C.type_steps_all(_type(C, key)).values())
    assert f"All {total} steps on." in page
    i = _idx(C, key, "booked", ("auto", "agreement_signed"))
    g = _idx(C, key, "shot", ("auto", "gallery_built"))
    pv = c.post(f"/settings/type/{key}/walk/{n}/preview", data={f"booked_{i}_on": "0"}, environ_base=E).get_json()
    assert pv["preview"] == f"{total - 1} of {total} steps on."
    assert ("auto", "agreement_signed") in C.WORKFLOW[key]["booked"]
    c.post(f"/settings/type/{key}/walk/{n}", data={"act": "save", f"booked_{i}_on": "0", f"shot_{g}_on": "0"},
           environ_base=E)
    assert ("auto", "agreement_signed") not in C.WORKFLOW[key]["booked"]
    assert ("auto", "gallery_built") in C.WORKFLOW[key]["shot"]
    nxt = c.get(f"/settings/type/{key}/walk/{n + 1}", environ_base=E).get_data(as_text=True)
    assert 'id="walk-defaults" disabled' in nxt

    c.post(f"/settings/type/{key}", data=dict(_form(C, key), shot_new="Charge batteries"), environ_base=E)
    page = c.get(f"/settings/type/{key}/walk/{n}", environ_base=E).get_data(as_text=True)
    assert "Charge batteries" in page and C.OWN_STEP_HELP in page

def test_the_walk_through_ends_on_approving_the_terms(tmp_path, monkeypatch):
    A, c = _app(tmp_path, monkeypatch, presets=("families",))
    import config as C
    import walkthrough as W
    key = "family"
    ids = [s["id"] for s in W.steps_for(dict(_type(C, key)))]
    last = c.get(f"/settings/type/{key}/walk/{len(ids) - 1}", environ_base=E).get_data(as_text=True)
    assert ">Next<" in last and "Accept all defaults" in last
    n = ids.index("delivery_days")
    c.post(f"/settings/type/{key}/walk/{n}", data={"act": "save", "delivery_days": "33"}, environ_base=E)
    doc = c.get(f"/settings/type/{key}/walk/{len(ids)}", environ_base=E).get_data(as_text=True)
    assert "Approve this document" in doc and "Not now" in doc and "SAMPLE" in doc
    assert "Your photos are ready within 33 days of the session" in doc
    with A.app.test_request_context():
        A.db()
        assert not A.doc_reviewed("terms", key)
    r = c.post(f"/settings/type/{key}/walk/approve", environ_base=E)
    assert r.status_code == 302 and f"/settings/type/{key}" in r.headers["Location"]
    with A.app.test_request_context():
        A.db()
        assert A.doc_reviewed("terms", key)
        st = json.loads(A.setting("walk"))[key]
    assert st.get("finished") and "changed" not in st
    again = c.get(f"/settings/type/{key}/walk/1", environ_base=E).get_data(as_text=True)
    assert 'id="walk-defaults" disabled' not in again
    done = c.get(f"/settings/type/{key}/walk/{len(ids)}", environ_base=E).get_data(as_text=True)
    assert "Approved" in done and "Approve this document" not in done
    assert not re.search(r"<script>", done)

def test_turning_the_codeword_off_carries_a_plain_warning(tmp_path, monkeypatch):

    A, c = _app(tmp_path, monkeypatch, presets=("portraits",))
    page = c.get("/settings/type/portrait", environ_base=E).get_data(as_text=True)
    assert 'data-off-warn="Without a passphrase, anyone who gets the gallery link can open the gallery' in page
    assert 'class="cw-off-note" data-off-note="f-gallery_codeword" hidden' in page
    types = [json.loads(json.dumps(t)) for t in __import__("config").TYPES]
    next(t for t in types if t["key"] == "portrait").setdefault("policy", {})["gallery_codeword"] = False
    with A.app.test_request_context():
        A.db()
        A._save_rules(TYPES=types)
        A.db().commit()
    page = c.get("/settings/type/portrait", environ_base=E).get_data(as_text=True)
    assert 'class="cw-off-note" data-off-note="f-gallery_codeword">' in page
