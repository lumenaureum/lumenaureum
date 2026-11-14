import math
from datetime import datetime, timedelta
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

def evening(day, lat=None, lon=None):

    if lat is None:
        if not HOME:
            return None, None
        lat, lon = HOME
    start = datetime(day.year, day.month, day.day, 12, 0, tzinfo=TZ)
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
