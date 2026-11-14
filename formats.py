from datetime import date, datetime, time

def clock(t):

    if isinstance(t, (datetime, time)):
        h, m = t.hour, t.minute
    else:
        try:
            h, m = (int(x) for x in str(t).strip().split(":")[:2])
        except (ValueError, AttributeError):
            return ""
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return ""
    return f"{h % 12 or 12}:{m:02d} {'am' if h < 12 else 'pm'}"

def month_label(ym):

    if isinstance(ym, date):
        return ym.strftime("%b %Y")
    try:
        y, m = (int(x) for x in str(ym).split("-")[:2])
        return date(y, m, 1).strftime("%b %Y")
    except (ValueError, TypeError):
        return ym or ""
