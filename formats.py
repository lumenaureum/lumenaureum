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

def money(v):

    try:
        v = round(float(v or 0), 2)
    except (TypeError, ValueError):
        v = 0.0
    a = abs(v)
    return ("-" if v < -0.004 else "") + f"${a:,.2f}"

def money_before_15(v):

    try:
        v = round(float(v or 0), 2)
    except (TypeError, ValueError):
        v = 0.0
    a = abs(v)
    return ("-" if v < 0 else "") + (f"${a:,.0f}" if a.is_integer() else f"${a:,.2f}")

CODEWORD_LEFT_OUT = "(the gallery codeword you chose with me)"

def drop_codewords(text, words):

    import re
    for w in sorted({" ".join((w or "").split()) for w in words if (w or "").strip()}, key=len, reverse=True):
        pat = r"\s+".join(re.escape(p) for p in w.split(" "))
        text = re.sub(pat, CODEWORD_LEFT_OUT, text, flags=re.IGNORECASE)
    return text
