import io
import json
import os
import sys
import zipfile
from decimal import Decimal

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import printorders as P

OID = "0123456789abcdef0123456789abcdef"

def good(**kw):
    d = dict(v=1, oid=OID, at="2026-11-20T15:00:00.000Z", plv=3, cur="USD",
             who=dict(name="Avery Sample", email="avery@example.com", phone=""),
             ship=dict(how="me", name="", addr1="12 Elm St", addr2="", city="Springfield", state="IL", zip="62704",
                       country="US", phone=""),
             lines=[dict(p="a" * 24 + ".bin", n="Lane-034.jpg", item="pl-3", qty=2, crop=[0, 0.1, 1, 0.8], turn=0,
                         each=3000, ppi=300)],
             sub=6000, ack=True, rel=dict(kind="new", prev=None), note="", pad="   ")
    d.update(kw)
    return d

def test_a_good_payload_passes_and_loses_its_padding():
    out = P.check_payload(good())
    assert out["oid"] == OID and "pad" not in out and out["lines"][0]["qty"] == 2 and out["sub"] == 6000
    assert P.check_payload(good(ship=dict(how="pickup")))["ship"] == {"how": "pickup"}

@pytest.mark.parametrize("change", [
    dict(v=2), dict(oid="0123"), dict(oid=OID.upper()), dict(plv=0), dict(plv=True), dict(cur="EUR"), dict(extra=1),
    dict(at="yesterday"), dict(who=dict(name="", email="a@b.co", phone="")),
    dict(who=dict(name="x" * 61, email="a@b.co", phone="")), dict(who=dict(name="A", email="nope", phone="")),
    dict(who=dict(name="A\x07B", email="a@b.co", phone="")), dict(who=dict(name=" A", email="a@b.co", phone="")),
    dict(ship=dict(how="drone")), dict(ship=dict(how="pickup", city="X")),
    dict(ship=dict(how="me", addr1="1 A St", city="X", state="ON", zip="62704", country="US")),
    dict(ship=dict(how="me", addr1="1 A St", city="X", state="IL", zip="6270", country="US")),
    dict(ship=dict(how="me", addr1="1 A St", city="X", state="IL", zip="62704", country="MX")),
    dict(ship=dict(how="other", name="", addr1="1 A St", city="X", state="IL", zip="62704", country="US")),
    dict(ship=dict(how="me", addr1="1 A St", city="X", state="ON", zip="K1A 0B1", country="CA", phone="")),
    dict(lines=[]), dict(lines=[dict(good()["lines"][0], qty=26)]), dict(lines=[dict(good()["lines"][0], qty=0)]),
    dict(lines=[dict(good()["lines"][0], qty=True)]), dict(lines=[dict(good()["lines"][0], p="../x.bin")]),
    dict(lines=[dict(good()["lines"][0], crop=[0, 0, 1.5, 1])]), dict(lines=[dict(good()["lines"][0], crop=[0.5, 0, 0.6, 1])]),
    dict(lines=[dict(good()["lines"][0], crop=[0, 0, 0.12345, 1])]), dict(lines=[dict(good()["lines"][0], turn=2)]),
    dict(lines=[dict(good()["lines"][0], item="bad id!")]), dict(lines=[dict(good()["lines"][0], each=-1)]),
    dict(lines=[dict(good()["lines"][0], extra=1)]), dict(sub=6001), dict(ack=False), dict(ack="yes"),
    dict(rel=dict(kind="maybe", prev=None)), dict(rel=dict(kind="change", prev="x")), dict(note="n" * 501),
    dict(pad="  x "),
])
def test_every_rule_refuses_when_broken(change):
    with pytest.raises(P.Refused):
        P.check_payload(good(**change))

def test_too_many_lines_and_canada_needs_a_phone():
    with pytest.raises(P.Refused):
        P.check_payload(good(lines=good()["lines"] * 51, sub=6000 * 51))
    ca = dict(how="me", name="", addr1="1 Rue A", addr2="", city="Ottawa", state="ON", zip="K1A 0B1", country="CA", phone="")
    with pytest.raises(P.Refused):
        P.check_payload(good(ship=ca))
    assert P.check_payload(good(ship=dict(ca, phone="613 555 0100")))["ship"]["country"] == "CA"

def test_padding_lands_on_the_five_sizes():
    assert [P.pad_size(n) for n in (1, 4096, 4097, 9000, 20000, 40000, 65536)] == [4096, 4096, 8192, 16384, 32768, 65536, 65536]
    assert P.pad_size(65537) is None
    assert P.host_name(b"x" * 10) == P.host_name(b"x" * 10) and len(P.host_name(b"y")) == 36

def test_the_fill_rounds_up_to_the_whole_dollar():
    assert P.fill_price(501) == 1600
    assert P.fill_price(500) == 1500
    assert P.fill_price(333, Decimal("3.5")) == 1200
    assert P.margin(1600, 501) == (1099, Decimal("3.2"))
    assert P.margin(1600, None) is None
    assert P.low_margin(700, 500) and not P.low_margin(750, 500) and not P.low_margin(700, None)
    for bad in ("0.5", "21", "abc", "3.25"):
        with pytest.raises(ValueError):
            P.markup_in(bad)
    assert P.markup_in("3") == 3 and P.cents("$1,200.5") == 120050 and P.cents("") is None
    with pytest.raises(ValueError):
        P.cents("12.345")
    with pytest.raises(ValueError):
        P.size_in("61")

@pytest.mark.parametrize("size", [(w, h) for _l, w, h, _e in P.STARTER])
@pytest.mark.parametrize("photo", [(6000, 4000), (4000, 6000), (5184, 3456), (3000, 3000), (4032, 3024)])
@pytest.mark.parametrize("turn", [0, 1])
def test_the_cut_is_the_exact_shape_in_whole_pixels(size, photo, turn):
    w, h = size
    ow, oh = photo
    crop = P.full_crop(ow, oh, w, h, turn)
    assert P.shape_ok(crop, ow, oh, w, h, turn)
    x, y, W, H = P.cut_rect(crop, ow, oh, w, h, turn)
    assert 0 <= x and 0 <= y and x + W <= ow and y + H <= oh
    land = P.frame_landscape(ow, oh, turn)
    a, b = P._ratio(w, h, land)
    assert W * b == H * a
    assert (W > H) == land or W == H

    assert W >= crop[2] * ow - a - 2 and H >= crop[3] * oh - b - 2

def test_ppi_and_fits():
    assert P.fits_without_crop(6000, 4000, 4, 6) and P.fits_without_crop(4000, 6000, 4, 6)
    assert not P.fits_without_crop(6000, 4000, 8, 10)
    crop = P.full_crop(6000, 4000, 8, 10)
    assert P.effective_ppi(crop, 6000, 4000, 8, 10, 0) in (499, 500)
    assert P.effective_ppi([0, 0, 0.25, 0.375], 6000, 4000, 8, 10, 0) == 150
    rect = P.cut_rect(crop, 6000, 4000, 8, 10, 0)
    assert P.cut_ppi(rect, 8, 10) in (499, 500)

def test_cut_jpeg_keeps_the_delivered_metadata_and_nothing_else():
    from PIL import Image
    im = Image.new("RGB", (300, 200), "red")
    exif = Image.Exif()
    exif[0x013B] = "Studio Name"
    b = io.BytesIO()
    im.save(b, "JPEG", exif=exif.tobytes(), comment=b"SECRET")
    data, qv = P.cut_jpeg(b.getvalue(), (10, 0, 160, 200))
    with Image.open(io.BytesIO(data)) as out:
        assert out.size == (160, 200) and qv == 95
        assert out.getexif().get(0x013B) == "Studio Name"
    assert b"SECRET" not in data
    _data, qv = P.cut_jpeg(b.getvalue(), (0, 0, 300, 200), max_bytes=100)
    assert qv == 85

def test_states_allow_only_the_designed_moves():
    allowed = {("new", "invoiced"), ("invoiced", "paid"), ("paid", "prepared"), ("prepared", "ordered"), ("paid", "ordered"),
               ("paid", "with_lab"), ("with_lab", "paid"), ("ordered", "shipped"), ("shipped", "delivered"),
               ("shipped", "remake"), ("delivered", "remake"), ("remake", "ordered"), ("delivered", "closed"),
               ("unmatched", "new"), ("invoiced", "expired"), ("expired", "new"), ("ordered", "canceled")}
    for a, b in allowed:
        assert P.may_move(a, b), (a, b)
    for a, b in [("new", "paid"), ("new", "ordered"), ("invoiced", "ordered"), ("closed", "new"), ("canceled", "paid"),
                 ("shipped", "paid"), ("unmatched", "invoiced"), ("ordered", "paid"), ("expired", "paid")]:
        assert not P.may_move(a, b), (a, b)
    assert P.may_move("with_lab", "ordered", "lab") and P.may_move("ordered", "shipped", "lab")
    for a, b in [("paid", "ordered"), ("paid", "with_lab"), ("ordered", "canceled"), ("new", "invoiced"), ("shipped", "closed")]:
        assert not P.may_move(a, b, "lab"), (a, b)

def test_the_address_reduction_keeps_city_state_and_zip():
    ship = dict(how="other", name="Grandma Rose", addr1="12 Elm St", addr2="Apt 3", city="Springfield", state="IL",
                zip="62704", country="US", phone="555 0100")
    r = P.reduce_ship(ship)
    assert r == dict(how="other", city="Springfield", state="IL", zip="62704", country="US", reduced=True)
    assert "Grandma" not in json.dumps(r) and "Elm" not in json.dumps(r)
    assert P.label_lines(r) == ["Springfield, IL 62704"]

def test_flags_and_labels():
    assert P.address_flags(dict(how="me", addr1="P.O. Box 12", city="X", state="HI", country="US"))[-2:] == [
        "A PO box: some labs and carriers cannot deliver there", "Ships to Alaska or Hawaii"]
    assert "Some letters may not print on a shipping label" in P.address_flags(dict(how="me", name="Zoë 李", addr1="x"))
    assert P.label_ok("Zoë Müller") and not P.label_ok("李")
    assert P.file_name("INV-2026-0012", 2, 10, 8, "Lustre Deep", 3) == "INV-2026-0012-2-8x10-Lustre-Deep-x3.jpg"
    assert P.shipping_charge({"rule": "cost"}, None) is None and P.shipping_charge({"rule": "cost"}, 812) == 812
    assert P.shipping_charge({"rule": "cost_plus", "handling": 300}, 812) == 1112
    assert P.shipping_charge({"rule": "flat", "flat": 900}, None) == 900
    assert P.tax_cents(6000, 6.25) == 375

def test_the_sheet_names_every_line_and_no_price_or_email():
    t = P.sheet_text(dict(invoice="INV-2026-0003", date="November 20, 2026", orderer="Avery Sample", note="Thanks!",
                          ship=dict(how="me", name="", addr1="12 Elm St", city="Springfield", state="IL", zip="62704",
                                    country="US", phone="555"),
                          lines=[dict(file="f1.jpg", name="Lane-034.jpg", label="8x10 print", w=8, h=10, finish="Lustre",
                                      qty=2, cropped=True, soft=True),
                                 dict(file="f2.jpg", name="Lane-035.jpg", label="4x6 print", w=4, h=6, finish="", qty=1,
                                      cropped=False, soft=False)]))
    assert "f1.jpg" in t and "f2.jpg" in t and "Number of prints: 3" in t and "Avery Sample" in t
    assert "May look soft" in t and "full frame" in t and "$" not in t and "@" not in t
    assert "8x10 print (8 x 10 in, Lustre), quantity 2" in t
    assert "4x6 print (4 x 6 in, standard finish), quantity 1" in t
    t2 = P.sheet_text(dict(invoice="INV-2026-0004", date="November 20, 2026", orderer="Avery Sample", note="",
                           ship=dict(how="pickup"),
                           lines=[dict(file="f1.jpg", name="Lane-034.jpg", label="5x7 print, Lustre", w=5, h=7, finish="Lustre",
                                       qty=1, cropped=False, soft=False)]))
    assert "5x7 print, Lustre (5 x 7 in), quantity 1" in t2 and t2.count("Lustre") == 1
    assert "Pick up from the studio" in t2

def test_the_zip_streams_one_file_at_a_time():
    made = []

    def mk(i):
        def f():
            made.append(i)
            return bytes([i]) * 1000
        return f
    gen = P.stream_zip([(f"f{i}.jpg", mk(i)) for i in range(3)])
    first = next(gen)
    assert made == [0] and first
    data = first + b"".join(gen)
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.namelist() == ["f0.jpg", "f1.jpg", "f2.jpg"] and z.read("f2.jpg") == b"\x02" * 1000
