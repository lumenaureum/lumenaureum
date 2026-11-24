import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import auth

def lock(th, ips):
    for ip in ips:
        th.fail(ip)

def test_sender_keys():
    assert auth.sender("192.0.2.7") == "192.0.2.7"
    assert auth.sender("2001:db8:1:2::5") == auth.sender("2001:db8:1:2:ffff:1:2:3") == "2001:db8:1:2::/64"
    assert auth.sender("2001:db8:1:3::5") != auth.sender("2001:db8:1:2::5")
    assert auth.sender("::ffff:192.0.2.7") == "192.0.2.7"
    assert auth.sender("fe80::1%eth0") == "fe80::/64"
    assert auth.sender("") == "" and auth.sender(None) == "" and auth.sender("localhost") == "localhost"

def test_ipv4_counted_per_address():
    th = auth.Throttle()
    lock(th, ["192.0.2.7"] * auth.MAX_FAILS)
    assert th.ip_blocked("192.0.2.7")
    assert not th.ip_blocked("192.0.2.8")

def test_ipv6_same_64_shares_the_hourly_count():
    th = auth.Throttle()
    lock(th, [f"2001:db8:1:2::{i:x}" for i in range(1, auth.IP_FAILS_PER_HOUR + 1)])
    assert th.ip_blocked("2001:db8:1:2::abcd")
    assert th.ip_blocked("2001:db8:1:2::1")
    assert "minutes" in th.wait_text("2001:db8:1:2::beef")

def test_ipv6_other_64_not_affected():
    th = auth.Throttle()
    lock(th, ["2001:db8:1:2::1"] * auth.MAX_FAILS)
    assert not th.ip_blocked("2001:db8:1:3::1")
    assert not th.ip_blocked("2001:db8:2:2::1")

def test_ipv4_mapped_is_the_ipv4_address():
    th = auth.Throttle()
    lock(th, ["::ffff:192.0.2.7"] * 3 + ["192.0.2.7"] * (auth.MAX_FAILS - 3))
    assert th.ip_blocked("192.0.2.7") and th.ip_blocked("::ffff:192.0.2.7")
    assert not th.ip_blocked("::ffff:192.0.2.8")

def test_success_clears_the_senders_count():
    th = auth.Throttle()
    lock(th, ["2001:db8:1:2::1"] * auth.MAX_FAILS)
    th.ok("2001:db8:1:2::1")
    assert not th.ip_blocked("2001:db8:1:2::1")
    lock(th, [f"2001:db8:1:2::{i:x}" for i in range(1, auth.IP_FAILS_PER_HOUR + 1)])
    th.ok("2001:db8:1:2::2")
    th.until.clear()
    assert not th.ip_blocked("2001:db8:1:2::1")

def test_hourly_ceiling_counts_the_64():
    th = auth.Throttle()
    lock(th, [f"2001:db8:1:2::{i:x}" for i in range(1, auth.IP_FAILS_PER_HOUR + 1)])
    th.until.clear()
    assert th.ip_blocked("2001:db8:1:2::ffff")

def test_report_keeps_exact_addresses():
    th = auth.Throttle()
    lock(th, ["2001:db8:1:2::1", "2001:db8:1:2::2"])
    assert [ip for _, ip, _ in th.take_report()] == ["2001:db8:1:2::1", "2001:db8:1:2::2"]
