CITY = "Riverton"
CITY_ZIPS = {"00501", "00502"}
CITY_TAX = "Riverton tax"

def inside(address):

    text = (address or "").strip()
    if not text:
        return False, "No address yet, so the city tax is left off. Add the client's address, or switch it on here."
    words = text.replace(",", " ").split()
    return CITY.lower() in text.lower() or any(w in CITY_ZIPS for w in words), ""

def rule(sale, taxes):

    tax = next((t for t in taxes if t["name"].lower() == CITY_TAX.lower()), None)
    if not tax:
        return {}
    on, warning = inside(sale.get("address"))
    return {tax["key"]: dict(on=on, note=warning)}

def register(ctx):
    ctx.hooks.tax_rules.append(rule)
