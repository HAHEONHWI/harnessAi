import datetime as dt
import unittest

from library.catalog import Catalog
from library.loans import LoanError, LoanService
from library.models import Book, Member


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog()
        self.catalog.add(Book("b1", "Dune", "Frank Herbert", 1965))
        self.loans = LoanService(self.catalog)
        self.loans.add_member(Member("m1", "Ann"))

    def test_checkout_sets_due_date(self):
        due = self.loans.checkout("b1", "m1", dt.date(2026, 1, 1))
        self.assertEqual(due, dt.date(2026, 1, 15))

    def test_double_checkout_fails(self):
        self.loans.checkout("b1", "m1", dt.date(2026, 1, 1))
        with self.assertRaises(LoanError):
            self.loans.checkout("b1", "m1", dt.date(2026, 1, 2))

    def test_search_title(self):
        self.assertEqual([b.id for b in self.catalog.search("Dune")], ["b1"])


if __name__ == "__main__":
    unittest.main()
