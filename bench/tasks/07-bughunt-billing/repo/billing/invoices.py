"""Invoice totals and numbering."""
from decimal import Decimal

from .money import round_half_up


class InvoiceBook:
    """Hands out invoice numbers INV-<year>-<4 digits>; the sequence restarts every year."""

    def __init__(self):
        self._last = 0

    def next_number(self, year):
        self._last += 1
        return f"INV-{year}-{self._last:04d}"


def build_invoice(lines, tax_rate, coupon_percent=0):
    """lines: [(description, cents)]. Discount = coupon_percent of the subtotal (rounded, never more than it).
    Tax is charged once on (subtotal - discount) at tax_rate, e.g. "0.0725"."""
    subtotal = sum(cents for _, cents in lines)
    discount = min(subtotal, round_half_up(Decimal(subtotal) * Decimal(str(coupon_percent)) / 100)) if subtotal > 0 else 0
    taxable = subtotal - discount
    share = Decimal(taxable) / subtotal if subtotal else Decimal(0)
    tax = sum(round_half_up(Decimal(cents) * share * Decimal(str(tax_rate))) for _, cents in lines)
    return {"subtotal": subtotal, "discount": discount, "tax": tax, "total": taxable + tax,
            "lines": [(d, c) for d, c in lines]}
