"""Order pricing for the web shop checkout."""
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
TAX_RATES = {"CA": Decimal("0.0725"), "NY": Decimal("0.08875"), "OR": Decimal("0"), "TX": Decimal("0.0625")}
FREE_SHIPPING_AT = Decimal("50.00")
SHIPPING = Decimal("5.99")


def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _line_total(item):
    qty = item["qty"]
    if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
        raise ValueError(f"invalid qty {qty!r}")
    price = Decimal(str(item["unit_price"]))
    if price < 0:
        raise ValueError("negative price")
    line = price * qty
    if qty >= 50:
        line *= Decimal("0.90")
    elif qty >= 10:
        line *= Decimal("0.95")
    return money(line)


def price_order(items, region, coupons=()):
    if region not in TAX_RATES:
        raise ValueError(f"unknown region {region}")
    subtotal = sum((_line_total(item) for item in items), Decimal("0.00"))
    kinds = [c.get("type") for c in coupons]
    if any(k not in ("percent", "fixed") for k in kinds) or len(set(kinds)) != len(kinds):
        raise ValueError("invalid coupons")
    remaining = subtotal
    for kind in ("percent", "fixed"):
        for coupon in coupons:
            if coupon["type"] != kind:
                continue
            value = Decimal(str(coupon["value"]))
            off = money(remaining * value / 100) if kind == "percent" else money(value)
            remaining = max(Decimal("0.00"), remaining - off)
    discount = subtotal - remaining
    tax = money(TAX_RATES[region] * remaining)
    shipping = Decimal("0.00") if not items or remaining >= FREE_SHIPPING_AT else SHIPPING
    return {
        "subtotal": money(subtotal),
        "discount": money(discount),
        "tax": tax,
        "shipping": money(shipping),
        "total": money(remaining + tax + shipping),
    }
