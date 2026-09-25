import datetime as dt
import unittest

from billing.invoices import InvoiceBook, build_invoice
from billing.ledger import Ledger
from billing.money import format_cents, to_cents
from billing.periods import period_for


class BillingTests(unittest.TestCase):
    def test_money_round_trip(self):
        self.assertEqual(to_cents("12.34"), 1234)
        self.assertEqual(format_cents(1234), "12.34")

    def test_period(self):
        self.assertEqual(period_for(dt.date(2026, 3, 20), 15), (dt.date(2026, 3, 15), dt.date(2026, 4, 14)))

    def test_invoice(self):
        inv = build_invoice([("plan", 1000)], "0.10")
        self.assertEqual((inv["subtotal"], inv["tax"], inv["total"]), (1000, 100, 1100))

    def test_numbers(self):
        book = InvoiceBook()
        self.assertEqual(book.next_number(2026), "INV-2026-0001")

    def test_ledger_balance(self):
        ledger = Ledger()
        ledger.record_invoice("A", 500, dt.date(2026, 1, 10))
        ledger.record_payment("A", 200, dt.date(2026, 1, 5))
        self.assertEqual(ledger.balance("A"), 300)


if __name__ == "__main__":
    unittest.main()
