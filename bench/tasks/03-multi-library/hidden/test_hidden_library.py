import datetime as dt
import unittest

from library.catalog import Catalog
from library.loans import LoanError, LoanService
from library.models import Book, Member
from library.reports import monthly_report

D = dt.date


def setup():
    catalog = Catalog()
    for book in [
        Book("b3", "The Hobbit", "J. R. R. Tolkien", 1937),
        Book("b1", "Dune", "Frank Herbert", 1965),
        Book("b2", "Dune Messiah", "Frank Herbert", 1969),
        Book("b4", "The Two Towers", "J. R. R. Tolkien", 1954),
        Book("b0", "Dune", "Brian Herbert", 2020),
    ]:
        catalog.add(book)
    loans = LoanService(catalog)
    for mid in ("m1", "m2", "m3", "m4"):
        loans.add_member(Member(mid, mid.upper()))
    return catalog, loans


class Search(unittest.TestCase):
    def ids(self, query):
        return [b.id for b in setup()[0].search(query)]

    def test_case_insensitive_title(self):
        self.assertEqual(self.ids("dune"), ["b0", "b1", "b2"])

    def test_author_match(self):
        self.assertEqual(self.ids("TOLKIEN"), ["b3", "b4"])

    def test_words_across_fields_any_order(self):
        self.assertEqual(self.ids("herbert messiah"), ["b2"])
        self.assertEqual(self.ids("tolkien two"), ["b4"])

    def test_all_words_required(self):
        self.assertEqual(self.ids("dune tolkien"), [])

    def test_blank_query_returns_all_sorted(self):
        self.assertEqual(self.ids("   "), ["b0", "b1", "b2", "b3", "b4"])


class Fines(unittest.TestCase):
    def test_on_time_is_free(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        self.assertEqual(loans.return_book("b1", D(2026, 1, 15)), 0)

    def test_per_day_and_member_total(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        self.assertEqual(loans.return_book("b1", D(2026, 1, 18)), 75)
        self.assertEqual(loans.members["m1"].fines_cents, 75)
        self.assertEqual(loans.events[-1]["fine_cents"], 75)

    def test_cap(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        self.assertEqual(loans.return_book("b1", D(2026, 6, 1)), 1000)

    def test_block_and_pay(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        loans.return_book("b1", D(2026, 2, 3))  # 19 days late -> 475
        loans.checkout("b2", "m1", D(2026, 2, 3))
        loans.return_book("b2", D(2026, 2, 18))  # 1 day late -> 25, total 500
        with self.assertRaises(LoanError):
            loans.checkout("b3", "m1", D(2026, 2, 18))
        loans.pay_fine("m1", 1)
        loans.checkout("b3", "m1", D(2026, 2, 18))
        self.assertEqual(loans.members["m1"].fines_cents, 499)

    def test_pay_validation(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        loans.return_book("b1", D(2026, 1, 16))
        for bad in (0, -5, 26):
            with self.assertRaises(ValueError):
                loans.pay_fine("m1", bad)
        loans.pay_fine("m1", 25)
        self.assertEqual(loans.members["m1"].fines_cents, 0)


class Reservations(unittest.TestCase):
    def test_rules(self):
        _, loans = setup()
        with self.assertRaises(LoanError):
            loans.reserve("b1", "m2", D(2026, 1, 1))  # not checked out
        loans.checkout("b1", "m1", D(2026, 1, 1))
        with self.assertRaises(LoanError):
            loans.reserve("b1", "m1", D(2026, 1, 2))  # already has it
        loans.reserve("b1", "m2", D(2026, 1, 2))
        with self.assertRaises(LoanError):
            loans.reserve("b1", "m2", D(2026, 1, 3))  # twice
        loans.reserve("b1", "m3", D(2026, 1, 3))
        self.assertEqual(loans.reservations("b1"), ["m2", "m3"])

    def test_hold_for_first_in_queue(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        loans.reserve("b1", "m2", D(2026, 1, 2))
        loans.reserve("b1", "m3", D(2026, 1, 3))
        loans.return_book("b1", D(2026, 1, 10))
        with self.assertRaises(LoanError):
            loans.checkout("b1", "m3", D(2026, 1, 11))
        with self.assertRaises(LoanError):
            loans.checkout("b1", "m4", D(2026, 1, 13))
        loans.checkout("b1", "m2", D(2026, 1, 13))  # last day of the hold
        self.assertEqual(loans.reservations("b1"), ["m3"])

    def test_hold_expires_to_next(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        loans.reserve("b1", "m2", D(2026, 1, 2))
        loans.reserve("b1", "m3", D(2026, 1, 3))
        loans.return_book("b1", D(2026, 1, 10))
        with self.assertRaises(LoanError):
            loans.checkout("b1", "m2", D(2026, 1, 14))  # m2's hold ended on the 13th; m3 holds 14..16
        with self.assertRaises(LoanError):
            loans.checkout("b1", "m4", D(2026, 1, 16))
        loans.checkout("b1", "m3", D(2026, 1, 16))
        self.assertEqual(loans.reservations("b1"), [])

    def test_all_holds_expire_then_anyone(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        loans.reserve("b1", "m2", D(2026, 1, 2))
        loans.reserve("b1", "m3", D(2026, 1, 3))
        loans.return_book("b1", D(2026, 1, 10))
        loans.checkout("b1", "m4", D(2026, 1, 17))
        self.assertEqual(loans.reservations("b1"), [])

    def test_no_queue_no_hold(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        loans.return_book("b1", D(2026, 1, 5))
        loans.checkout("b1", "m4", D(2026, 1, 5))

    def test_late_return_with_reservation(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 1))
        loans.reserve("b1", "m2", D(2026, 1, 2))
        self.assertEqual(loans.return_book("b1", D(2026, 1, 20)), 125)
        loans.checkout("b1", "m2", D(2026, 1, 21))
        self.assertEqual(loans.due_date("b1"), D(2026, 2, 4))


class Reports(unittest.TestCase):
    def build(self):
        _, loans = setup()
        loans.checkout("b1", "m1", D(2026, 1, 30))
        loans.return_book("b1", D(2026, 2, 16))  # 3 days late -> 75, in February
        loans.checkout("b1", "m2", D(2026, 2, 16))
        loans.checkout("b2", "m3", D(2026, 2, 1))
        loans.return_book("b2", D(2026, 2, 10))
        loans.checkout("b2", "m3", D(2026, 2, 11))
        loans.checkout("b3", "m4", D(2026, 2, 2))
        loans.checkout("b4", "m1", D(2026, 2, 3))
        loans.checkout("b0", "m2", D(2026, 3, 1))
        return loans.events

    def test_counts_and_fines(self):
        report = monthly_report(self.build(), 2026, 2)
        self.assertEqual(report["checkouts"], 5)
        self.assertEqual(report["returns"], 2)
        self.assertEqual(report["fines_cents"], 75)

    def test_top_books(self):
        report = monthly_report(self.build(), 2026, 2)
        self.assertEqual([tuple(x) for x in report["top_books"]], [("b2", 2), ("b1", 1), ("b3", 1)])

    def test_other_month(self):
        report = monthly_report(self.build(), 2026, 1)
        self.assertEqual((report["checkouts"], report["returns"], report["fines_cents"]), (1, 0, 0))
        self.assertEqual([tuple(x) for x in report["top_books"]], [("b1", 1)])

    def test_empty_month(self):
        report = monthly_report(self.build(), 2025, 12)
        self.assertEqual(report, {"checkouts": 0, "returns": 0, "fines_cents": 0, "top_books": []})


if __name__ == "__main__":
    unittest.main()
