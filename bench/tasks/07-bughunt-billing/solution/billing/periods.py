"""Monthly billing periods anchored on the customer's signup day."""
import calendar
import datetime as dt


def anchor_date(year, month, anchor_day):
    """The anchor day in that month, clamped to the month's last day (31 -> Feb 28/29)."""
    return dt.date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def _shift(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def period_for(day, anchor_day):
    """(start, end) of the billing period containing day; end is inclusive."""
    start = anchor_date(day.year, day.month, anchor_day)
    if day < start:
        start = anchor_date(*_shift(day.year, day.month, -1), anchor_day)
    nxt = anchor_date(*_shift(start.year, start.month, 1), anchor_day)
    return start, nxt - dt.timedelta(days=1)


def days_inclusive(start, end):
    return (end - start).days + 1


def prorate(monthly_cents, used_start, used_end, period):
    """Charge for the days used_start..used_end (inclusive) of a (start, end) period."""
    from .money import round_half_up
    from decimal import Decimal
    used = days_inclusive(used_start, used_end)
    total = days_inclusive(*period)
    return round_half_up(Decimal(monthly_cents) * used / total)
