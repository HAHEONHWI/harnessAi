import datetime as dt
import unittest

from billing.invoices import InvoiceBook, build_invoice
from billing.ledger import Ledger
from billing.money import allocate, format_cents, to_cents
from billing.periods import period_for, prorate

D = dt.date


class Amounts(unittest.TestCase):
    def test_one_decimal(self):
        self.assertEqual(to_cents("0.1"), 10)
        self.assertEqual(to_cents("12.5"), 1250)
        self.assertEqual(to_cents("-3.4"), -340)

    def test_existing_parsing_kept(self):
        self.assertEqual(to_cents(" 1,234.05 "), 123405)
        self.assertEqual(to_cents("7"), 700)
        for bad in ("1.234", "abc", "", "1..2"):
            with self.assertRaises(ValueError, msg=bad):
                to_cents(bad)

    def test_negative_format(self):
        self.assertEqual(format_cents(-5), "-0.05")
        self.assertEqual(format_cents(-123450), "-1,234.50")
        self.assertEqual(format_cents(-100), "-1.00")

    def test_positive_format_kept(self):
        self.assertEqual(format_cents(0), "0.00")
        self.assertEqual(format_cents(123456789), "1,234,567.89")


class Periods(unittest.TestCase):
    def test_short_month_clamps(self):
        self.assertEqual(period_for(D(2026, 2, 10), 31), (D(2026, 1, 31), D(2026, 2, 27)))
        self.assertEqual(period_for(D(2026, 2, 28), 31), (D(2026, 2, 28), D(2026, 3, 30)))
        self.assertEqual(period_for(D(2028, 2, 29), 30), (D(2028, 2, 29), D(2028, 3, 29)))

    def test_month_end_boundaries(self):
        self.assertEqual(period_for(D(2026, 4, 30), 31), (D(2026, 4, 30), D(2026, 5, 30)))
        self.assertEqual(period_for(D(2026, 5, 31), 31), (D(2026, 5, 31), D(2026, 6, 29)))

    def test_regular_periods_kept(self):
        self.assertEqual(period_for(D(2026, 1, 5), 15), (D(2025, 12, 15), D(2026, 1, 14)))
        self.assertEqual(period_for(D(2026, 1, 15), 15), (D(2026, 1, 15), D(2026, 2, 14)))


class Proration(unittest.TestCase):
    def test_single_day(self):
        period = (D(2026, 4, 1), D(2026, 4, 30))
        self.assertEqual(prorate(3000, D(2026, 4, 30), D(2026, 4, 30), period), 100)

    def test_inclusive_ranges(self):
        period = (D(2026, 1, 15), D(2026, 2, 14))  # 31 days
        self.assertEqual(prorate(3100, D(2026, 1, 15), D(2026, 2, 14), period), 3100)
        self.assertEqual(prorate(1000, D(2026, 2, 1), D(2026, 2, 14), period), 452)


class Allocation(unittest.TestCase):
    def test_parts_sum_to_total(self):
        for total, weights in ((100, [1, 1, 1]), (1001, [3, 3, 4]), (7, [1] * 6), (-100, [1, 1, 1]), (99999, [7, 11, 13, 17])):
            parts = allocate(total, weights)
            self.assertEqual(sum(parts), total, (total, weights))

    def test_largest_remainder_and_ties(self):
        self.assertEqual(allocate(100, [1, 1, 1]), [34, 33, 33])
        self.assertEqual(allocate(10, [1, 2, 2]), [2, 4, 4])
        self.assertEqual(allocate(5, [1, 1, 1, 1]), [2, 1, 1, 1])
        self.assertEqual(allocate(-100, [1, 1, 1]), [-34, -33, -33])

    def test_validation_kept(self):
        for weights in ([], [0, 0], [1, -1]):
            with self.assertRaises(ValueError):
                allocate(10, weights)


class Invoices(unittest.TestCase):
    def test_tax_once_on_discounted_subtotal(self):
        lines = [(f"usage {i}", 5) for i in range(10)]  # 10 x 5 cents
        inv = build_invoice(lines, "0.0725")
        self.assertEqual((inv["subtotal"], inv["tax"], inv["total"]), (50, 4, 54))
        inv = build_invoice([("a", 333), ("b", 333), ("c", 334)], "0.0825", coupon_percent=10)
        self.assertEqual((inv["discount"], inv["tax"], inv["total"]), (100, 74, 974))

    def test_discount_rules_kept(self):
        inv = build_invoice([("a", 1000)], "0", coupon_percent=150)
        self.assertEqual((inv["discount"], inv["total"]), (1000, 0))
        inv = build_invoice([], "0.1")
        self.assertEqual((inv["subtotal"], inv["tax"], inv["total"]), (0, 0, 0))

    def test_numbering_per_year(self):
        book = InvoiceBook()
        self.assertEqual([book.next_number(2026) for _ in range(3)][-1], "INV-2026-0003")
        self.assertEqual(book.next_number(2027), "INV-2027-0001")
        self.assertEqual(book.next_number(2026), "INV-2026-0004")
        self.assertEqual(book.next_number(2027), "INV-2027-0002")


class Late(unittest.TestCase):
    def ledger(self):
        ledger = Ledger()
        ledger.record_invoice("A", 1000, D(2026, 3, 10))
        return ledger

    def test_paid_on_last_grace_day_is_on_time(self):
        ledger = self.ledger()
        ledger.record_payment("A", 1000, D(2026, 3, 13))
        self.assertFalse(ledger.is_late("A", D(2026, 4, 1)))
        self.assertFalse(ledger.is_late("A", D(2026, 4, 1), grace_days=3))

    def test_late_cases(self):
        ledger = self.ledger()
        self.assertFalse(ledger.is_late("A", D(2026, 3, 13)))
        self.assertTrue(ledger.is_late("A", D(2026, 3, 14)))
        ledger.record_payment("A", 1000, D(2026, 3, 14))
        self.assertTrue(ledger.is_late("A", D(2026, 5, 1)))
        self.assertFalse(self.ledger().is_late("A", D(2026, 3, 10), grace_days=0))

    def test_partial_payment_stays_open(self):
        ledger = self.ledger()
        ledger.record_payment("A", 600, D(2026, 3, 12))
        ledger.record_payment("A", 399, D(2026, 3, 12))
        self.assertEqual(ledger.balance("A"), 1)
        self.assertIsNone(ledger.paid_on("A"))
        self.assertEqual(ledger.open_invoices(D(2026, 3, 12)), ["A"])
        ledger.record_payment("A", 1, D(2026, 3, 20))
        self.assertEqual(ledger.paid_on("A"), D(2026, 3, 20))
        self.assertEqual(ledger.open_invoices(D(2026, 3, 20)), [])


if __name__ == "__main__":
    unittest.main()
