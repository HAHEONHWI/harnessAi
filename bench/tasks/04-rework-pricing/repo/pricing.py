"""Order pricing for the web shop checkout."""

TAX_RATES = {"CA": 0.0725, "NY": 0.08875}


def price_order(items, region):
    subtotal = 0
    for item in items:
        subtotal += item["unit_price"] * item["qty"]
    tax = subtotal * TAX_RATES.get(region, 0)
    if subtotal > 50:
        shipping = 0
    else:
        shipping = 5.99
    return {
        "subtotal": round(subtotal, 2),
        "tax": round(tax, 2),
        "shipping": shipping,
        "total": round(subtotal + tax + shipping, 2),
    }
