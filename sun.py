import gzip
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("UTC")
HOME = None

def elevation(dt_utc, lat, lon):
    jd = dt_utc.timestamp() / 86400 + 2440587.5
    t = (jd - 2451545) / 36525
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t)) + math.sin(2 * m) * (0.019993 - 0.000101 * t)
         + math.sin(3 * m) * 0.000289)
    lam = math.radians(l0 + c - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * t)))
    eps = math.radians(23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
                       + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * t)))
    decl = math.asin(math.sin(eps) * math.sin(lam))
    y = math.tan(eps / 2) ** 2
    l0r = math.radians(l0)
    eot = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(m) + 4 * e * y * math.sin(m) * math.cos(2 * l0r)
                           - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * m))
    minutes = dt_utc.hour * 60 + dt_utc.minute + dt_utc.second / 60
    ha = math.radians(((minutes + eot + 4 * lon) / 4) - 180)
    latr = math.radians(lat)
    return math.degrees(math.asin(math.sin(latr) * math.sin(decl) + math.cos(latr) * math.cos(decl) * math.cos(ha)))

def evening(day, lat=None, lon=None, tz=None):

    if lat is None:
        if not HOME:
            return None, None
        lat, lon = HOME
    start = datetime(day.year, day.month, day.day, 12, 0, tzinfo=tz or TZ)
    golden = sunset = None
    for i in range(0, 10 * 60):
        t = start + timedelta(minutes=i)
        el = elevation(t.astimezone(ZoneInfo("UTC")), lat, lon)
        if golden is None and el <= 6:
            golden = t
        if el <= -0.833:
            sunset = t
            break
    return golden, sunset

def fmt(t):
    return t.strftime("%-I:%M") if t else ""

_STATE_ZIP3 = {
    "AL": "350-369", "AK": "995-999", "AZ": "850-865", "AR": "716-729", "CA": "900-961", "CO": "800-816",
    "CT": "060-069", "DE": "197-199", "DC": "200-205,569", "FL": "320-339,341-349", "GA": "300-319,398-399",
    "HI": "967-968", "ID": "832-838", "IL": "600-629", "IN": "460-479", "IA": "500-528", "KS": "660-679",
    "KY": "400-427", "LA": "700-714", "ME": "039-049", "MD": "206-219", "MA": "010-027,055", "MI": "480-499",
    "MN": "550-567", "MS": "386-397", "MO": "630-658", "MT": "590-599", "NE": "680-693", "NV": "889-898",
    "NH": "030-038", "NJ": "070-089", "NM": "870-884", "NY": "100-149", "NC": "270-289", "ND": "580-588",
    "OH": "430-459", "OK": "730-749", "OR": "970-979", "PA": "150-196", "RI": "028-029", "SC": "290-299",
    "SD": "570-577", "TN": "370-385", "TX": "750-799,885", "UT": "840-847", "VT": "050-054,056-059",
    "VA": "201,220-246", "WA": "980-994", "WV": "247-268", "WI": "530-549", "WY": "820-831", "PR": "006-007,009",
}

_STATE_TZ = {
    "AL": "America/Chicago", "AK": "America/Anchorage", "AZ": "America/Phoenix", "AR": "America/Chicago",
    "CA": "America/Los_Angeles", "CO": "America/Denver", "CT": "America/New_York", "DE": "America/New_York",
    "DC": "America/New_York", "GA": "America/New_York", "HI": "Pacific/Honolulu", "IL": "America/Chicago",
    "IA": "America/Chicago", "LA": "America/Chicago", "ME": "America/New_York", "MD": "America/New_York",
    "MA": "America/New_York", "MN": "America/Chicago", "MS": "America/Chicago", "MO": "America/Chicago",
    "MT": "America/Denver", "NV": "America/Los_Angeles", "NH": "America/New_York", "NJ": "America/New_York",
    "NM": "America/Denver", "NY": "America/New_York", "NC": "America/New_York", "OH": "America/New_York",
    "OK": "America/Chicago", "PA": "America/New_York", "RI": "America/New_York", "SC": "America/New_York",
    "UT": "America/Denver", "VT": "America/New_York", "VA": "America/New_York", "WA": "America/Los_Angeles",
    "WV": "America/New_York", "WI": "America/Chicago", "WY": "America/Denver", "PR": "America/Puerto_Rico",
}
_ZIP3_TZ = {"FL": {"324": "America/Chicago", "325": "America/Chicago", "*": "America/New_York"},
            "TX": {"798": "America/Denver", "799": "America/Denver", "885": "America/Denver", "*": "America/Chicago"},
            "MI": {"498": None, "499": None, "*": "America/Detroit"},
            "NE": {"690": None, "691": None, "692": None, "693": None, "*": "America/Chicago"},
            "OR": {"979": None, "*": "America/Los_Angeles"}}
_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR"}
_PLACE_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(_STATE_NAMES, key=len, reverse=True)) + r"|[A-Z]{2})"
                       r"\.?[ ,]+(\d{5})(?:-\d{4})?(?!\d)", re.I)
_ZIPS = None
NEAR_KM = 80

def _zip_table():
    global _ZIPS
    if _ZIPS is None:
        try:
            with gzip.open(Path(__file__).resolve().parent / "zip_latlon.json.gz", "rt") as f:
                _ZIPS = json.load(f)["zips"]
        except (OSError, ValueError, KeyError):
            _ZIPS = {}
    return _ZIPS

def _in_state(st, z3):
    for part in _STATE_ZIP3.get(st, "").split(","):
        a, _, b = part.partition("-")
        if part and a <= z3 <= (b or a):
            return True
    return False

def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(min(1.0, h)))

def place_of(text):

    for m in _PLACE_RE.finditer(text or ""):
        word, z = m.group(1), m.group(2)
        st = _STATE_NAMES.get(word.lower()) or (word.upper() if len(word) == 2 and word.isupper() else "")
        ll = _zip_table().get(z) if st else None
        if not ll or not _in_state(st, z[:3]):
            continue
        ll = (float(ll[0]), float(ll[1]))
        if HOME and _km(HOME, ll) <= NEAR_KM:
            return ll[0], ll[1], None
        by3 = _ZIP3_TZ.get(st)
        tz = (by3.get(z[:3], by3["*"]) if by3 else _STATE_TZ.get(st))
        if tz:
            return ll[0], ll[1], ZoneInfo(tz)
    return None
