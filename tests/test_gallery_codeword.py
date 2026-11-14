import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
VEC = json.load(open(os.path.join(ROOT, "tests", "vectors", "gallery-codeword.json")))

def test_vectors_and_normalizing():
    from plugins import gallery as G
    assert G.CODEWORD_ROUNDS == VEC["rounds"] == 600_000
    for v in VEC["vectors"]:
        assert G.norm_codeword(v["word"]) == v["normalized"]
        assert G.codeword_key(v["link_key"], v["gallery_id"], v["word"]) == v["key"]
    a, b = VEC["vectors"][0], VEC["vectors"][1]
    assert a["key"] == b["key"]
    assert G.codeword_key(a["link_key"], a["gallery_id"], "") == a["link_key"]
    assert G.codeword_key(a["link_key"], "galOTHER0000", a["word"]) != a["key"]
