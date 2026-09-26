"""Money is always handled as integer cents."""
import re
from decimal import ROUND_HALF_UP, Decimal

AMOUNT_RE = re.compile(r"([+-]?)(\d+)(?:\.(\d{1,2}))?")


def to_cents(text):
    """'12.5' -> 1250, '-0.05' -> -5. More than two decimals or anything else raises ValueError."""
    m = AMOUNT_RE.fullmatch(str(text).strip().replace(",", ""))
    if not m:
        raise ValueError(f"not an amount: {text!r}")
    sign = -1 if m.group(1) == "-" else 1
    fraction = (m.group(3) or "").ljust(2, "0")
    return sign * (int(m.group(2)) * 100 + int(fraction))


def format_cents(cents):
    """1234567 -> '12,345.67', -5 -> '-0.05'."""
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{part:02d}"


def round_half_up(value):
    """Round a Decimal/number of cents to a whole cent, halves away from zero."""
    return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def allocate(total, weights):
    """Split total cents in proportion to weights; the parts always add up to total.
    Leftover cents go to the largest remainders, earlier weights first on ties."""
    if not weights or any(w < 0 for w in weights) or sum(weights) == 0:
        raise ValueError("weights must be non-negative with a positive sum")
    whole = sum(weights)
    sign = -1 if total < 0 else 1
    amount = abs(total)
    shares = [amount * w // whole for w in weights]
    remainders = [amount * w % whole for w in weights]
    left = amount - sum(shares)
    for i in sorted(range(len(weights)), key=lambda i: (-remainders[i], i))[:left]:
        shares[i] += 1
    return [sign * s for s in shares]
