import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers_tools import need_tool
from helpers import host_write
import test_orders_php as OP
from test_orders_php import host
import test_viewer_prints as VP

def iso(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

@pytest.fixture(scope="module")
def res():
    need_tool("node")
    _priv, spk = VP.keys()
    return VP.run({
        "far": {"window_only": True, "manifest_headers": {"X-Sunset": iso(24 * 5)}},
        "inside": {"window_only": True, "manifest_headers": {"X-Sunset": iso(10)}},
        "no_sunset": {"window_only": True},
        "left_open": {"manifest_headers": {"X-Sunset": iso(24 * 5)}, "statuses": [423]},
    }, spk)

def test_outside_the_window_prints_can_be_ordered_and_the_notice_gives_the_deadline(res):
    far = res["far"]
    assert far["pp_wired"] and "closed" not in far["prline"]
    assert "It closes on " in far["sswhen"] and " Order prints by " in far["sswhen"] and far["sswhen"].endswith(".")
    assert res["no_sunset"]["pp_wired"] and "Order prints by" not in res["no_sunset"]["sswhen"]

def test_inside_the_last_24_hours_ordering_is_closed_right_there(res):
    inside = res["inside"]
    assert not inside["pp_wired"]
    assert inside["prline"] == ("Ordering prints has closed because this gallery closes within 24 hours. To order prints, "
                                "email studio@example.com to ask for the gallery to be extended.")
    assert "Order prints by" not in inside["sswhen"]

def test_a_page_left_open_is_told_when_the_host_refuses(res):
    out = res["left_open"]
    assert len(out["posts"]) == 1
    assert "Ordering prints has closed because this gallery closes within 24 hours" in out["said"][-1]

    assert out["said"][-1].startswith("Your order was not sent")
    assert "so this order was not sent." in out["said"][-1]
    assert out["said"][-1].count("studio@example.com") == 1

def test_a_short_gallery_says_order_prints_by_before_ordering_closes():

    from plugins import gallery as G
    assert [G.sunset_days(d) for d in (1, 2, 3, 4, 5, 8, 14, 30)] == [1, 2, 2, 2, 2, 2, 4, 7]
    assert all(G.sunset_days(d) >= 2 for d in range(2, 61))

def test_orders_php_refuses_inside_the_last_24_hours(host):
    base, d, proof, G, key, spk = host
    u = base + "orders.php"
    good = OP.body(OP.order(G, key, spk, "1" * 32))
    end = int(time.time()) + 3600 * 10
    host_write(d, "limits.txt", f"days=30\nunopened_until=2099-01-01\nuntil_ts={end}\nproof=1\n")
    st, _b, h = OP.call(u, good, proof)
    assert st == 423 and h.get("X-Prints-Closed")
    assert OP.kept(d) == []
    host_write(d, "limits.txt", f"days=30\nunopened_until=2099-01-01\nuntil_ts={end + 86400}\nproof=1\n")
    assert OP.call(u, good, proof)[0] == 204
    assert len(OP.kept(d)) == 1

