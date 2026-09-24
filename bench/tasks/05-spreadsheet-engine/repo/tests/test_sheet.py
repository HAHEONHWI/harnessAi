import unittest

from sheet import Sheet


class SheetTests(unittest.TestCase):
    def test_literals(self):
        s = Sheet()
        s.set("A1", "5")
        s.set("A2", "hello")
        self.assertEqual(s.get("A1"), 5)
        self.assertEqual(s.get("A2"), "hello")
        self.assertIsNone(s.get("A3"))

    def test_simple_formula(self):
        s = Sheet()
        s.set("A1", "2")
        s.set("A2", "=A1*3+1")
        self.assertEqual(s.get("A2"), 7)


if __name__ == "__main__":
    unittest.main()
